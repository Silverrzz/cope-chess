from enum import Enum
import time

class TimeControlCategory(Enum):
    INCREMENT = 1
    MOVETIME = 2
    MOVESTOGO = 3
    MOVENODES = 4

class TimeControl:
    def __init__(
        self,
        category: TimeControlCategory,
        *,
        initial_time: int | None = None,
        increment: int | None = None,
        move_time: int | None = None,
        moves_to_go: int | None = None,
        nodes: int | None = None,
    ):
        self.category = category

        self.chess_clock = ChessClock()

        if category is TimeControlCategory.INCREMENT:
            if initial_time is None or increment is None:
                raise ValueError("Increment mode needs initial_time and increment")
            self.initial_time = initial_time
            self.increment = increment

        elif category is TimeControlCategory.MOVETIME:
            if move_time is None:
                raise ValueError("Movetime mode needs move_time")
            self.move_time = move_time

        elif category is TimeControlCategory.MOVESTOGO:
            if initial_time is None or moves_to_go is None:
                raise ValueError("Moves-to-go mode needs initial_time and moves_to_go")
            self.initial_time = initial_time
            self.moves_to_go = moves_to_go

        elif category is TimeControlCategory.MOVENODES:
            if nodes is None:
                raise ValueError("Movenodes mode needs nodes")
            self.nodes = nodes

    def get_manager_object(self):
        return TimeManager(self)
    
class TimeOutError(Exception):
    pass

class TimeManager(TimeControl):
    def __init__(self, tc: TimeControl):
        super().__init__(
            category=tc.category,
            initial_time=getattr(tc, "initial_time", None),
            increment=getattr(tc, "increment", None),
            move_time=getattr(tc, "move_time", None),
            moves_to_go=getattr(tc, "moves_to_go", None),
            nodes=getattr(tc, "nodes", None),
        )

    def start_clock(self):
        self.chess_clock.start_clock()

    def probe_clock(self):
        elapsed_time = self.chess_clock.get_elapsed_time()
        if self.category is TimeControlCategory.INCREMENT:
            remaining_time = round(self.initial_time - elapsed_time)

            if remaining_time < 0:
                raise TimeOutError()

            return remaining_time
            
        elif self.category is TimeControlCategory.MOVETIME:
            remaining_time = round(self.move_time - elapsed_time)

            if remaining_time < 0:
                raise TimeOutError()

            return remaining_time
            
        elif self.category is TimeControlCategory.MOVESTOGO:
            
            remaining_time = round(self.initial_time - elapsed_time)

            if remaining_time < 0:
                raise TimeOutError()

            return remaining_time

        elif self.category is TimeControlCategory.MOVENODES:
            return self.nodes

    def stop_clock(self):
        elapsed_time = self.chess_clock.stop_clock()
        if self.category is TimeControlCategory.INCREMENT:
            self.initial_time = round(self.initial_time - elapsed_time)

            if self.initial_time < 0:
                raise TimeOutError()

            self.initial_time += self.increment
            
        elif self.category is TimeControlCategory.MOVETIME:
            self.move_time = round(self.move_time - elapsed_time)

            if self.move_time < 0:
                raise TimeOutError()
            
        elif self.category is TimeControlCategory.MOVESTOGO:
            
            self.initial_time = round(self.initial_time - elapsed_time)
            self.moves_to_go -= 1

            if self.initial_time < 0:
                raise TimeOutError()

        elif self.category is TimeControlCategory.MOVENODES:
            pass

class ChessClock():
    def __init__(self, start_time: float = 0, running: bool = False):
        self.start_time = start_time
        self.running = running
        self.clock = self.get_time()

    def start_clock(self):
        self.clock = self.get_time()
        self.running = True

    def probe_clock(self):
        elapsed_time = self.get_elapsed_time()
        self.clock = self.get_time()

        return elapsed_time
        
    def stop_clock(self):
        elapsed_time = self.probe_clock()
        self.running = False
        return elapsed_time

    def get_time(self):
        return time.perf_counter_ns() / 1_000_000

    def get_elapsed_time(self):
        if not self.running:
            return 0

        return self.get_time() - self.clock
