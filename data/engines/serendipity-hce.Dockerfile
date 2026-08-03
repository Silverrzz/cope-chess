FROM maven:3.9.16-eclipse-temurin-17-noble AS builder

WORKDIR /build

COPY . .

RUN --mount=type=cache,target=/root/.m2 \
    sed -i \
        -e '/import java.io.IOException;/d' \
        -e '/import org.shawn.games.Serendipity.NNUE/d' \
        -e '/private static NNUE network;/d' \
        -e '/public static class NNUEOption/,/public static class HashOption/{/public static class HashOption/!d;}' \
        -e '/StringOption networkName =/d' \
        -e '112,121d' \
        -e 's/engine.init(threads.get(), transpositionTable, network)/engine.init(threads.get(), transpositionTable)/g' \
        -e '/AccumulatorStack acc =/d' \
        -e '/acc.init(internalBoard);/d' \
        -e 's/NNUE.evaluate(internalBoard, network, acc)/engine.getMainThread().evaluate(internalBoard)/' \
        Serendipity/src/main/java/org/shawn/games/Serendipity/UCI/UCI.java \
    && sed -i \
        -e '/import org.shawn.games.Serendipity.NNUE.NNUE;/d' \
        -e '/NNUE network;/d' \
        -e 's/init(threadsCount, tt, network)/init(threadsCount, tt)/' \
        -e '/public void reinit(NNUE network)/,+4d' \
        -e 's/public void init(int threadsCount, TranspositionTable tt, NNUE network)/public void init(int threadsCount, TranspositionTable tt)/' \
        -e '/this.network = network;/d' \
        -e 's/new SharedThreadData(tt, startBarrier, endBarrier, network, this.stopped)/new SharedThreadData(tt, startBarrier, endBarrier, this.stopped)/' \
        Serendipity/src/main/java/org/shawn/games/Serendipity/Search/ThreadManager.java \
    && sed -i \
        -e '/import org.shawn.games.Serendipity.NNUE.NNUE;/d' \
        -e '/final NNUE network;/d' \
        -e 's/public SharedThreadData(TranspositionTable tt, CyclicBarrier startBarrier, CyclicBarrier endBarrier, NNUE network,/public SharedThreadData(TranspositionTable tt, CyclicBarrier startBarrier, CyclicBarrier endBarrier,/' \
        -e '/this.network = network;/d' \
        Serendipity/src/main/java/org/shawn/games/Serendipity/Search/SharedThreadData.java \
    && sed -i \
        -e '/import org.shawn.games.Serendipity.NNUE.\*;/d' \
        -e '/private AccumulatorStack accumulators;/d' \
        -e 's/AccumulatorDiff diff = board.doMove(move);/board.doMove(move);/' \
        -e '/accumulators/d' \
        Serendipity/src/main/java/org/shawn/games/Serendipity/Search/AlphaBeta.java \
    && sed -i \
        -e 's/public AccumulatorDiff doMove(final Move move)/public void doMove(final Move move)/' \
        -e '/AccumulatorDiff diff = new AccumulatorDiff();/d' \
        -e '/diff\./d' \
        -e '/return diff;/d' \
        Serendipity/src/main/java/org/shawn/games/Serendipity/Chess/Board.java \
    && sed -i \
        -e '/<compilerArgs>/,/<\/compilerArgs>/d' \
        -e '/<argLine>--add-modules=jdk.incubator.vector<\/argLine>/d' \
        Serendipity/pom.xml \
    && rm -rf Serendipity/src/main/java/org/shawn/games/Serendipity/NNUE \
    && rm -f Serendipity/src/main/java/org/shawn/games/Serendipity/Chess/AccumulatorDiff.java \
    && ! grep -R -E 'NNUE|AccumulatorDiff|AccumulatorStack' Serendipity/src/main/java \
    && mvn -B -ntp -f Serendipity/pom.xml -Dmaven.test.skip=true package \
    && install -Dm644 Serendipity/target/Serendipity-Test.jar /jpackage-input/engine.jar \
    && jpackage \
        --type app-image \
        --name Serendipity \
        --input /jpackage-input \
        --main-jar engine.jar \
        --dest /opt/cope \
        --add-modules java.base \
        --jlink-options "--strip-native-commands --strip-debug --no-man-pages --no-header-files --compress=2" \
        --java-options -XX:+UseParallelGC \
    && ln -s Serendipity/bin/Serendipity /opt/cope/engine

FROM debian:bookworm-slim

WORKDIR /opt/cope
COPY --from=builder /opt/cope/ ./

ENTRYPOINT ["./engine"]
