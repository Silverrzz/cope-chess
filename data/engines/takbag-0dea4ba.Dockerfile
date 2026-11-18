FROM debian:bookworm-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake g++ make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY . .
COPY <<'TEI' source/tak/tei.h
#pragma once

#include "Tps.h"
#include "engine/Engine.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <string_view>

void tei(const OptionMap&)
{
    std::size_t size = 6;
    double komi = 0;
    Game game(size, komi);
    std::string line;

    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string command;
        in >> command;
        if (command == "tei")
        {
            std::cout << "id name Takbag\nid author Ally\n"
                      << "option name HalfKomi type spin default 0 min 0 max 4\nteiok" << std::endl;
        }
        else if (command == "isready")
            std::cout << "readyok" << std::endl;
        else if (command == "quit")
            return;
        else if (command == "setoption")
        {
            std::string name, option, value;
            int halfKomi = 0;
            if (in >> name >> option >> value >> halfKomi && name == "name"
                && option == "HalfKomi" && value == "value" && halfKomi >= 0 && halfKomi <= 4)
                komi = halfKomi / 2.0;
        }
        else if (command == "teinewgame")
        {
            in >> size;
            game = Game(size, komi);
        }
        else if (command == "position")
        {
            std::string source, next;
            in >> source;
            if (source == "startpos")
                game = Game(size, komi);
            else if (source == "tps")
            {
                std::string board, side, fullmove;
                in >> board >> side >> fullmove;
                game = gameFromTps(board + " " + side + " " + fullmove, komi);
            }
            else
                continue;
            if (in >> next && next == "moves")
                while (in >> next)
                    game.play(next);
        }
        else if (command == "go")
        {
            int clock = 0, increment = 0, movetime = 0, depth = 15;
            const bool white = game.getPosition().getPlayer() == Player::White;
            const std::string_view timeKey = white ? "wtime" : "btime";
            const std::string_view incKey = white ? "winc" : "binc";
            std::string key;
            int value;
            while (in >> key >> value)
            {
                if (key == timeKey)
                    clock = value;
                else if (key == incKey)
                    increment = value;
                else if (key == "movetime")
                    movetime = value;
                else if (key == "depth")
                    depth = std::max(1, value);
            }
            const int budget = movetime > 0 ? movetime : clock > 0
                ? std::clamp(clock / 10 + increment / 2, 1, std::max(1, clock - 10)) : 1000;
            Engine engine;
            const auto start = std::chrono::steady_clock::now();
            const auto move = engine.chooseMove(game.getPosition(), budget / 1000.0, depth);
            const auto elapsed = std::chrono::steady_clock::now() - start;
            const auto micros = std::max<int64_t>(1, std::chrono::duration_cast<std::chrono::microseconds>(elapsed).count());
            const auto nodes = engine.getStats().mSeenNodes;
            std::cout << "info time " << micros / 1000 << " nodes " << nodes
                      << " nps " << nodes * 1000000 / micros << " pv " << move << '\n'
                      << "bestmove " << move << std::endl;
        }
    }
}
TEI
RUN sed -i '/^template <typename LeftEnum, typename RightEnum>$/a\    requires std::is_enum_v<LeftEnum> \&\& std::is_enum_v<RightEnum>' source/other/EnumBitOps.h \
    && cmake -S . -B out -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-march=x86-64-v3 -include cstdint -include limits" -DCMAKE_EXE_LINKER_FLAGS="-static" \
    && cmake --build out --target tak --parallel 2 \
    && install -Dm755 out/source/tak/tak /opt/cope/takbag

FROM debian:bookworm-slim
WORKDIR /opt/cope
COPY --from=builder /opt/cope/takbag ./takbag
COPY --chmod=0755 <<'EOF' ./engine
#!/bin/sh
set -eu
engine="${0%/*}/takbag"
if [ "${1-}" != bench ]; then
    exec "$engine" "$@"
fi
output=$(printf '%s\n' tei 'setoption name HalfKomi value 4' 'teinewgame 6' 'position startpos moves a1 f6 c3 d4 Sc4 Cd3' 'go movetime 10000 depth 4' quit | "$engine")
printf '%s\n' "$output"
printf '%s\n' "$output" | awk '
    /^info / { for (i=2;i<NF;i++) { if ($i=="nodes") nodes=$(i+1); if ($i=="nps") nps=$(i+1) } }
    /^bestmove / { done=1 }
    END { if (!done || nodes<=0 || nps<=0) exit 1; printf "Nodes searched: %d\nNodes per second: %d\n", nodes, nps }
'
EOF
ENTRYPOINT ["./engine"]
