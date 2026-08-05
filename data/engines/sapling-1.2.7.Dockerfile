FROM mcr.microsoft.com/dotnet/sdk:9.0.305-bookworm-slim AS builder

WORKDIR /build
COPY . .

RUN --mount=type=cache,target=/root/.nuget/packages \
    sed -i 's|Path.Combine(Environment.CurrentDirectory, "logs")|Path.Combine(Path.GetTempPath(), "sapling-logs")|' src/Sapling/Program.cs \
    && dotnet publish src/Sapling/Sapling.csproj \
        --configuration Release \
        --runtime linux-x64 \
        --self-contained true \
        --output /publish \
        -p:Release=true \
        -p:ExecutableName=engine \
        -p:InvariantGlobalization=true \
        -p:DebugType=None \
        -p:DebugSymbols=false \
    && install -Dm755 /publish/engine /opt/cope/engine

FROM mcr.microsoft.com/dotnet/runtime-deps:9.0-bookworm-slim

ENV DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/.net

WORKDIR /tmp
COPY --from=builder /opt/cope/engine /opt/cope/engine

ENTRYPOINT ["/opt/cope/engine"]
