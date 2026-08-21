FROM eclipse-temurin:21-jdk-jammy AS builder

WORKDIR /build
COPY . .

RUN --mount=type=cache,target=/root/.gradle \
    sed -i 's/\r$//' gradlew \
    && chmod +x gradlew \
    && ./gradlew --no-daemon shadowJar -x test \
    && install -Dm644 build/libs/Erinn-1.1.jar /opt/cope/engine.jar

FROM eclipse-temurin:21-jre-jammy

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine.jar ./engine.jar

ENTRYPOINT ["java", "-XX:+UseParallelGC", "-jar", "./engine.jar"]
