FROM eclipse-temurin:21-jdk-jammy AS builder

COPY EubosChess/bin/Eubos.jar /jpackage-input/engine.jar

RUN cd /jpackage-input \
    && echo "146fb9e22d9582210091d039d13a9af408be0353f6508b6400a8722b48d04381  engine.jar" | sha256sum -c - \
    && jpackage \
        --type app-image \
        --name Eubos \
        --input /jpackage-input \
        --main-jar engine.jar \
        --dest /opt/cope \
        --add-modules java.base,java.instrument,java.management,jdk.attach,jdk.unsupported \
        --jlink-options "--strip-native-commands --strip-debug --no-man-pages --no-header-files --compress=2" \
        --java-options -Xshare:off \
        --java-options -XX:MaxInlineSize=40 \
        --java-options -Djdk.attach.allowAttachSelf \
    && ln -s Eubos/bin/Eubos /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
