FROM eclipse-temurin:21-jre-jammy

WORKDIR /opt/cope
COPY EubosChess/bin/Eubos.jar ./engine.jar

RUN echo "146fb9e22d9582210091d039d13a9af408be0353f6508b6400a8722b48d04381  engine.jar" | sha256sum -c -

ENTRYPOINT ["java", "-Xshare:off", "-XX:MaxInlineSize=40", "-Djdk.attach.allowAttachSelf", "-jar", "./engine.jar"]
