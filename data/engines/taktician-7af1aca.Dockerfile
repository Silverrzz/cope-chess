FROM golang:1.24.6-bookworm AS builder

WORKDIR /build
COPY . .
ENV CGO_ENABLED=0 GOAMD64=v3 GOTOOLCHAIN=local

COPY <<'PATCH' /tmp/cope-tei.patch
--- a/tak/game.go
+++ b/tak/game.go
@@ -14,0 +15 @@
+	HalfKomi int
@@ -40,0 +42,8 @@
+}
+
+func (p *Position) WithHalfKomi(halfKomi int) *Position {
+	next := p.Clone()
+	cfg := *p.cfg
+	cfg.HalfKomi = halfKomi
+	next.cfg = &cfg
+	return next
@@ -306 +315,2 @@
-	if cw > cb {
+	white, black := 2*cw, 2*cb+p.cfg.HalfKomi
+	if white > black {
@@ -309 +319 @@
-	if cb > cw {
+	if black > white {
--- a/tei/server.go
+++ b/tei/server.go
@@ -27,0 +28 @@
+	halfKomi int
@@ -54,0 +56 @@
+			fmt.Fprintln(e.out, "option name HalfKomi type spin default 0 min 0 max 4")
@@ -57,0 +60,8 @@
+		case "setoption":
+			if len(words) == 5 && words[1] == "name" && words[2] == "HalfKomi" && words[3] == "value" {
+				value, err := strconv.Atoi(words[4])
+				if err != nil || value < 0 || value > 4 {
+					return fmt.Errorf("invalid HalfKomi: %s", words[4])
+				}
+				e.halfKomi = value
+			}
@@ -74,0 +85 @@
+			e.pos = e.pos.WithHalfKomi(e.halfKomi)
@@ -215 +226,5 @@
-	fmt.Fprintf(e.out, "info depth %d time %d nodes %d score cp %d pv%s\n",
+	elapsed := uint64(stats.Elapsed.Nanoseconds())
+	if elapsed == 0 {
+		elapsed = 1
+	}
+	fmt.Fprintf(e.out, "info depth %d time %d nodes %d nps %d score cp %d pv%s\n",
@@ -218,0 +234 @@
+		stats.Visited * 1000000000 / elapsed,
PATCH
RUN git apply --unidiff-zero /tmp/cope-tei.patch \
    && go build -mod=readonly -trimpath -ldflags="-s -w" -o /opt/cope/taktician ./cmd/taktician

FROM debian:bookworm-slim
WORKDIR /opt/cope
COPY --from=builder /opt/cope/taktician ./taktician
COPY --from=builder /build/COPYING ./COPYING
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/sh
set -eu
engine="${0%/*}/taktician"
if [ "${1-}" != bench ]; then
    exec "$engine" tei "$@"
fi
output=$(printf '%s\n' tei 'setoption name HalfKomi value 4' 'teinewgame 6' 'position startpos moves a1 f6 c3 d4 Sc4 Cd3' go quit | "$engine" tei -depth 4)
printf '%s\n' "$output"
printf '%s\n' "$output" | awk '
    /^info / { for (i=2;i<NF;i++) { if ($i=="nodes") nodes=$(i+1); if ($i=="nps") nps=$(i+1) } }
    /^bestmove / { done=1 }
    END { if (!done || nodes<=0 || nps<=0) exit 1; printf "Nodes searched: %d\nNodes per second: %d\n", nodes, nps }
'
EOF
ENTRYPOINT ["./engine"]
