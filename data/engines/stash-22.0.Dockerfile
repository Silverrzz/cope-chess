FROM gcc:14.3.0-bookworm AS builder

WORKDIR /build
COPY . .

RUN make -C src -j2 CC=gcc ARCH=x86-64-bmi2 EXT_OFLAGS="-march=x86-64-v3 -mtune=generic -fcommon -Wno-error" EXT_LFLAGS="-static" \
    && install -Dm755 src/stash-bot /opt/cope/engine-native \
    && strip /opt/cope/engine-native

COPY <<'EOF' /tmp/cope-bench.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
    char native[4096];
    ssize_t length = readlink("/proc/self/exe", native, sizeof(native) - 32);
    if (length < 0) return 127;
    native[length] = 0;
    char *slash = strrchr(native, '/');
    if (!slash) return 127;
    strcpy(slash + 1, "engine-native");
    if (argc != 2 || strcmp(argv[1], "bench")) {
        execv(native, argv);
        return 127;
    }
    int p[2];
    if (pipe(p)) return 1;
    pid_t child = fork();
    if (child < 0) return 1;
    if (!child) {
        close(p[0]);
        dup2(p[1], 1); dup2(p[1], 2); close(p[1]);
        execl(native, "engine-native", "bench", NULL);
        _exit(127);
    }
    close(p[1]);
    FILE *r = fdopen(p[0], "r");
    if (!r) return 1;
    char line[16384]; unsigned long long nps = 0, value;
    while (fgets(line, sizeof(line), r)) {
        if (sscanf(line, "NPS: %llu", &value) == 1) nps = value;
    }
    fclose(r);
    int status;
    if (waitpid(child, &status, 0) < 0 || !WIFEXITED(status) || WEXITSTATUS(status) || !nps) return 1;
    printf("nps: %llu\n", nps);
    return 0;
}
EOF

RUN gcc -O2 -static -o /opt/cope/engine /tmp/cope-bench.c && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
