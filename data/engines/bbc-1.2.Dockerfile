FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .
COPY <<'COPE_BENCH' /tmp/cope-bench.c
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static const char *const positions[] = {
    "position startpos",
    "position startpos moves e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6",
    "position startpos moves d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7",
    "position startpos moves e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6",
    "position startpos moves d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6",
    "position startpos moves c2c4 e7e5 b1c3 g8f6 g2g3 d7d5 c4d5 f6d5",
    "position startpos moves e2e4 e7e6 d2d4 d7d5 b1c3 f8b4 e4e5 c7c5",
    "position startpos moves e2e4 c7c6 d2d4 d7d5 b1c3 d5e4 c3e4 c8f5",
};

static int send_command(FILE *input, const char *command) {
    return fputs(command, input) >= 0 && fputc('\n', input) != EOF && fflush(input) == 0;
}

static int wait_for(FILE *output, const char *marker, uint64_t *nodes) {
    char line[16384];
    size_t size = strlen(marker);
    while (fgets(line, sizeof(line), output)) {
        if (!strncmp(line, marker, size) && isspace((unsigned char)line[size])) return 1;
        if (!nodes || strncmp(line, "info ", 5) != 0) continue;
        const char *field = strstr(line, " nodes ");
        if (!field || !isdigit((unsigned char)field[7])) continue;
        errno = 0;
        uint64_t value = strtoull(field + 7, NULL, 10);
        if (!errno && value > *nodes) *nodes = value;
    }
    return 0;
}

static int run_bench(const char *native) {
    int to_engine[2], from_engine[2];
    if (pipe(to_engine) || pipe(from_engine)) return 1;
    pid_t child = fork();
    if (child < 0) return 1;
    if (child == 0) {
        if (dup2(to_engine[0], STDIN_FILENO) < 0 || dup2(from_engine[1], STDOUT_FILENO) < 0) _exit(126);
        close(to_engine[0]); close(to_engine[1]); close(from_engine[0]); close(from_engine[1]);
        execl(native, native, (char *)NULL);
        _exit(127);
    }
    close(to_engine[0]); close(from_engine[1]);
    FILE *input = fdopen(to_engine[1], "w"), *output = fdopen(from_engine[0], "r");
    if (!input || !output) return 1;
    int ok = send_command(input, "uci") && wait_for(output, "uciok", NULL)
        && send_command(input, "setoption name Threads value 1")
        && send_command(input, "setoption name Hash value 16")
        && send_command(input, "setoption name OwnBook value false");
    double elapsed = 0.0;
    uint64_t total_nodes = 0;
    for (size_t i = 0; ok && i < sizeof(positions) / sizeof(positions[0]); i++) {
        struct timespec start, end;
        uint64_t nodes = 0;
        ok = send_command(input, "ucinewgame") && send_command(input, "isready")
            && wait_for(output, "readyok", NULL) && send_command(input, positions[i])
            && clock_gettime(CLOCK_MONOTONIC, &start) == 0 && send_command(input, "go depth 11")
            && wait_for(output, "bestmove", &nodes) && clock_gettime(CLOCK_MONOTONIC, &end) == 0;
        if (!ok || !nodes) { ok = 0; break; }
        total_nodes += nodes;
        elapsed += (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9;
    }
    if (ok) ok = send_command(input, "quit");
    fclose(input); fclose(output);
    int status;
    if (waitpid(child, &status, 0) < 0 || !WIFEXITED(status) || WEXITSTATUS(status) || !ok || elapsed <= 0) {
        fputs("UCI benchmark did not finish with node counts for all positions\n", stderr);
        return 1;
    }
    printf("NPS: %" PRIu64 "\n", (uint64_t)(total_nodes / elapsed));
    return 0;
}

int main(int argc, char **argv) {
    char native[4096];
    ssize_t size = readlink("/proc/self/exe", native, sizeof(native) - 32);
    if (size < 1 || size >= (ssize_t)sizeof(native) - 32) return 127;
    native[size] = '\0';
    char *separator = strrchr(native, '/');
    if (!separator) return 127;
    strcpy(separator + 1, "engine-native");
    if (argc == 2 && !strcmp(argv[1], "bench")) return run_bench(native);
    argv[0] = native;
    execv(native, argv);
    return 127;
}
COPE_BENCH
RUN mkdir -p /opt/cope \
    && gcc -std=gnu11 -O3 -DNDEBUG -march=x86-64-v3 -static src/bbc_1.2.c -lm -o /opt/cope/engine-native \
    && sed -n '47,80p' src/bbc_1.2.c > /opt/cope/LICENSE \
    && gcc -std=c11 -O2 -static /tmp/cope-bench.c -o /opt/cope/engine \
    && strip /opt/cope/engine /opt/cope/engine-native

FROM scratch
WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./
ENTRYPOINT ["./engine"]
