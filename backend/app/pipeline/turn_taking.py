"""Turn-taking: decides when the user has finished speaking and when to
interrupt the agent (barge-in). Energy-based VAD placeholder; swap in Silero VAD."""
from dataclasses import dataclass


@dataclass
class TurnState:
    speaking: bool = False
    silence_ms: int = 0
    end_of_turn_ms: int = 600


class TurnTaker:
    def __init__(self, end_of_turn_ms: int = 600, energy_threshold: float = 500.0):
        self.s = TurnState(end_of_turn_ms=end_of_turn_ms)
        self.threshold = energy_threshold

    @staticmethod
    def energy(frame: bytes) -> float:
        import array

        a = array.array("h", frame[: len(frame) // 2 * 2])
        return (sum(x * x for x in a) / max(len(a), 1)) ** 0.5

    def push(self, frame: bytes, frame_ms: int = 20) -> str:
        """Returns 'speech', 'silence' or 'end_of_turn'."""
        if self.energy(frame) > self.threshold:
            self.s.speaking, self.s.silence_ms = True, 0
            return "speech"
        if not self.s.speaking:
            return "silence"
        self.s.silence_ms += frame_ms
        if self.s.silence_ms >= self.s.end_of_turn_ms:
            self.s.speaking, self.s.silence_ms = False, 0
            return "end_of_turn"
        return "silence"
