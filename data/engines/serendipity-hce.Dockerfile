FROM maven:3.9.16-eclipse-temurin-17-noble AS builder

WORKDIR /build

ADD --checksum=sha256:165bb50864a9e872c5a268d7a23471cb45e0fbacdc7bf19f5e9a03d636529f48 https://raw.githubusercontent.com/xu-shawn/Serendipity-Networks/bfd3862fa2aa1570018fa71de8f86ab10ead8047/net2.nnue /build/Serendipity/src/main/resources/embedded.nnue
COPY . .

RUN --mount=type=cache,target=/root/.m2 \
    mvn -B -ntp -f Serendipity/pom.xml -Dmaven.test.skip=true package \
    && install -Dm644 Serendipity/target/Serendipity-Test.jar /jpackage-input/engine.jar \
    && jpackage \
        --type app-image \
        --name Serendipity \
        --input /jpackage-input \
        --main-jar engine.jar \
        --dest /opt/cope \
        --add-modules jdk.incubator.vector \
        --java-options --add-modules=jdk.incubator.vector \
        --java-options -XX:+UseParallelGC \
    && ln -s Serendipity/bin/Serendipity /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
