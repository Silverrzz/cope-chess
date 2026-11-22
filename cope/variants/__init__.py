from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import chess

from .duck import DuckBoard, DuckTurn
from .tak import TAK_CAPS, TAK_FLATS, TAK_HALF_KOMI, TAK_START, TakBoard, TakMove


VariantId = Literal["standard", "dfrc", "duck", "duck960", "tak"]
Position = chess.Board | DuckBoard | TakBoard
Turn = chess.Move | DuckTurn | TakMove


@dataclass(frozen=True, slots=True)
class VariantDefinition:
    id: VariantId
    name: str
    rules_revision: str
    chess960: bool = False
    duck: bool = False
    requires_openings: bool = False
    engine_protocol: Literal["uci", "tei"] = "uci"

    def board(self, fen: str | None = None) -> Position:
        if self.engine_protocol == "tei":
            return TakBoard(TAK_START if not fen or fen == "startpos" else fen)
        fen = chess.STARTING_FEN if not fen or fen == "startpos" else fen
        return DuckBoard(fen, chess960=self.chess960) if self.duck else chess.Board(fen, chess960=self.chess960)

    def uci_options(self) -> dict[str, str | int | bool]:
        if self.engine_protocol == "tei":
            return {"HalfKomi": TAK_HALF_KOMI, "Flats": TAK_FLATS, "Caps": TAK_CAPS}
        options: dict[str, str | int | bool] = {}
        options["UCI_Variant"] = "duck" if self.duck else "chess"
        options["UCI_Chess960"] = self.chess960
        return options

    def pgn_headers(self) -> dict[str, str]:
        if self.duck:
            return {"Variant": "Duck960" if self.chess960 else "Duck", "VariantRules": self.rules_revision}
        return {"Variant": "Chess960", "CopeVariant": "DFRC"} if self.chess960 else {}


VARIANTS = {item.id: item for item in (
    VariantDefinition("standard", "Standard", "chess1"),
    VariantDefinition("dfrc", "DFRC", "chess1", chess960=True, requires_openings=True),
    VariantDefinition("duck", "Duck", "duck1", duck=True),
    VariantDefinition("duck960", "Duck960", "duck1", chess960=True, duck=True, requires_openings=True),
    VariantDefinition("tak", "Tak 6×6 +2", "tak1", engine_protocol="tei"),
)}


def get_variant(variant: str = "standard") -> VariantDefinition:
    try:
        return VARIANTS[variant]
    except KeyError as exc:
        raise ValueError(f"Unknown variant: {variant}") from exc


def create_board(fen: str | None = None, *, variant: str = "standard") -> Position:
    return get_variant(variant).board(fen)


def parse_turn(board: Position, text: str) -> Turn:
    return board.parse_uci(text)
