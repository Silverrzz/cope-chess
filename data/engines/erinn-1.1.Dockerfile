FROM eclipse-temurin:21-jdk-jammy AS builder

WORKDIR /build
COPY . .

RUN --mount=type=cache,target=/root/.gradle \
    sed -i 's/\r$//' gradlew \
    && chmod +x gradlew \
    && ./gradlew --no-daemon shadowJar -x test \
    && install -Dm644 build/libs/Erinn-1.1.jar /jpackage-input/engine.jar \
    && jpackage \
        --type app-image \
        --name Erinn \
        --input /jpackage-input \
        --main-jar engine.jar \
        --dest /opt/cope \
        --add-modules java.base \
        --jlink-options "--strip-native-commands --strip-debug --no-man-pages --no-header-files --compress=2" \
        --java-options -XX:+UseParallelGC \
    && ln -s Erinn/bin/Erinn /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
