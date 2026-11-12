FROM debian:trixie-slim AS builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y gcc libc6-dev ocaml-nox ocaml-dune ocaml-findlib libmtime-ocaml-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

COPY <<'EOF' /build/bin/echekinator.ml
open Libs.Uci

let bench () =
  let process = Unix.open_process_args_full Sys.executable_name [|Sys.executable_name|] (Unix.environment ()) in
  let input, output, _ = process in
  let send command = output_string output (command ^ "\n"); flush output in
  let rec wait expected =
    if input_line input <> expected then wait expected
  in
  send "uci";
  wait "uciok";
  send "setoption name Hash value 16";
  send "setoption name Threads value 1";
  send "isready";
  wait "readyok";
  let total_nodes = ref 0 and total_ms = ref 0 in
  List.iter (fun position ->
    send "ucinewgame";
    send position;
    send "isready";
    wait "readyok";
    send "go depth 14";
    let nodes = ref 0 and milliseconds = ref 0 in
    let rec fields = function
      | "nodes" :: value :: rest -> nodes := int_of_string value; fields rest
      | "time" :: value :: rest -> milliseconds := int_of_string value; fields rest
      | _ :: rest -> fields rest
      | [] -> ()
    in
    let rec read () =
      let line = input_line input in
      if String.starts_with ~prefix:"bestmove " line then ()
      else begin
        if String.starts_with ~prefix:"info " line then
          fields (String.split_on_char ' ' line);
        read ()
      end
    in
    read ();
    if !nodes <= 0 || !milliseconds <= 0 then failwith "invalid benchmark counters";
    total_nodes := !total_nodes + !nodes;
    total_ms := !total_ms + !milliseconds
  ) [
    "position startpos";
    "position fen r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1";
    "position fen 8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"
  ];
  send "quit";
  match Unix.close_process_full process with
  | Unix.WEXITED 0 -> Printf.printf "nodes: %d\nnps: %d\n%!" !total_nodes (!total_nodes * 1000 / !total_ms)
  | _ -> failwith "benchmark engine failed"

let () =
  if Array.length Sys.argv > 1 && Sys.argv.(1) = "bench" then bench ()
  else echekinator ()
EOF

RUN sed -i 's/(libraries libs))/(libraries libs) (link_flags -ccopt -static))/' bin/dune \
    && dune build --profile release bin/echekinator.exe \
    && install -Dm755 _build/default/bin/echekinator.exe /opt/cope/engine \
    && strip /opt/cope/engine

FROM scratch

WORKDIR /opt/cope
COPY --from=builder /opt/cope/engine ./engine

ENTRYPOINT ["./engine"]
