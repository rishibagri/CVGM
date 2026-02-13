"""
Lightweight MIDI file parser.

Parses Standard MIDI Files (format 0 and 1) without external dependencies.
Extracts note-on/off events with absolute timing in seconds.
"""

import struct
from dataclasses import dataclass, field
from typing import BinaryIO, List, Optional


@dataclass
class NoteEvent:
    """A single note event with timing."""
    note: int           # MIDI note number 0-127
    velocity: int       # 0-127
    time_on: float      # seconds
    time_off: float     # seconds
    channel: int = 0
    track: int = 0

    @property
    def duration(self) -> float:
        return self.time_off - self.time_on

    @property
    def octave(self) -> int:
        return (self.note // 12) - 1

    @property
    def pitch_class(self) -> int:
        return self.note % 12

    @property
    def name(self) -> str:
        names = ["C", "C#", "D", "D#", "E", "F",
                 "F#", "G", "G#", "A", "A#", "B"]
        return f"{names[self.pitch_class]}{self.octave}"

    @property
    def is_black_key(self) -> bool:
        return self.pitch_class in (1, 3, 6, 8, 10)


@dataclass
class MidiData:
    """Parsed MIDI file data."""
    notes: List[NoteEvent] = field(default_factory=list)
    tempo_bpm: float = 120.0
    ticks_per_beat: int = 480
    duration_seconds: float = 0.0
    num_tracks: int = 1

    @property
    def note_range(self):
        if not self.notes:
            return (60, 72)
        lo = min(n.note for n in self.notes)
        hi = max(n.note for n in self.notes)
        return (lo, hi)

    def notes_in_range(self, time_start: float, time_end: float) -> List[NoteEvent]:
        return [n for n in self.notes
                if n.time_on < time_end and n.time_off > time_start]

    def notes_for_hand(self, hand: str, split_note: int = 60) -> List[NoteEvent]:
        """Split notes between left and right hand at split_note (middle C)."""
        if hand == "LEFT":
            return [n for n in self.notes if n.note < split_note]
        else:
            return [n for n in self.notes if n.note >= split_note]


# ---------------------------------------------------------------------------
# Binary helpers
# ---------------------------------------------------------------------------

def _read_bytes(f: BinaryIO, n: int) -> bytes:
    data = f.read(n)
    if len(data) < n:
        raise EOFError(f"Expected {n} bytes, got {len(data)}")
    return data


def _read_u16(f: BinaryIO) -> int:
    return struct.unpack(">H", _read_bytes(f, 2))[0]


def _read_u32(f: BinaryIO) -> int:
    return struct.unpack(">I", _read_bytes(f, 4))[0]


def _read_variable_length(f: BinaryIO) -> int:
    """Read a MIDI variable-length quantity."""
    value = 0
    while True:
        byte = struct.unpack("B", _read_bytes(f, 1))[0]
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return value


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_midi(filepath: str, bpm_override: float = 0.0) -> MidiData:
    """Parse a Standard MIDI File and return structured note data."""
    result = MidiData()

    with open(filepath, "rb") as f:
        # --- Header chunk ---
        header_id = _read_bytes(f, 4)
        if header_id != b"MThd":
            raise ValueError(f"Not a MIDI file (header: {header_id!r})")

        header_len = _read_u32(f)
        midi_format = _read_u16(f)
        num_tracks = _read_u16(f)
        ticks_per_beat = _read_u16(f)

        # Skip any extra header bytes
        if header_len > 6:
            f.read(header_len - 6)

        result.num_tracks = num_tracks
        result.ticks_per_beat = ticks_per_beat

        # Tempo map: list of (tick, microseconds_per_beat)
        tempo_map = [(0, 500000)]  # default 120 BPM

        # Collect raw events from all tracks
        all_events = []  # (absolute_tick, event_type, data, track_idx)

        for track_idx in range(num_tracks):
            track_id = _read_bytes(f, 4)
            if track_id != b"MTrk":
                raise ValueError(f"Expected MTrk, got {track_id!r}")
            track_len = _read_u32(f)
            track_end = f.tell() + track_len

            abs_tick = 0
            running_status = 0

            while f.tell() < track_end:
                delta = _read_variable_length(f)
                abs_tick += delta

                # Peek at status byte
                peek = struct.unpack("B", _read_bytes(f, 1))[0]

                if peek == 0xFF:
                    # Meta event
                    meta_type = struct.unpack("B", _read_bytes(f, 1))[0]
                    meta_len = _read_variable_length(f)
                    meta_data = _read_bytes(f, meta_len)

                    if meta_type == 0x51 and meta_len == 3:
                        # Tempo change
                        uspb = (meta_data[0] << 16) | (meta_data[1] << 8) | meta_data[2]
                        tempo_map.append((abs_tick, uspb))
                    elif meta_type == 0x2F:
                        # End of track
                        break

                elif peek == 0xF0 or peek == 0xF7:
                    # SysEx
                    sysex_len = _read_variable_length(f)
                    _read_bytes(f, sysex_len)

                else:
                    # Channel message
                    if peek & 0x80:
                        status = peek
                        running_status = status
                    else:
                        status = running_status
                        # peek was data byte, rewind conceptually
                        # We already consumed it, use it as first data byte
                        f.seek(f.tell() - 1)
                        _read_bytes(f, 1)  # re-read it
                        data1 = peek
                        msg_type = (status >> 4) & 0x0F
                        channel = status & 0x0F
                        if msg_type in (0x8, 0x9, 0xA, 0xB, 0xE):
                            data2 = struct.unpack("B", _read_bytes(f, 1))[0]
                            all_events.append((abs_tick, msg_type, channel,
                                               data1, data2, track_idx))
                        elif msg_type in (0xC, 0xD):
                            all_events.append((abs_tick, msg_type, channel,
                                               data1, 0, track_idx))
                        continue

                    msg_type = (status >> 4) & 0x0F
                    channel = status & 0x0F

                    if msg_type in (0x8, 0x9, 0xA, 0xB, 0xE):
                        data1 = struct.unpack("B", _read_bytes(f, 1))[0]
                        data2 = struct.unpack("B", _read_bytes(f, 1))[0]
                        all_events.append((abs_tick, msg_type, channel,
                                           data1, data2, track_idx))
                    elif msg_type in (0xC, 0xD):
                        data1 = struct.unpack("B", _read_bytes(f, 1))[0]
                        all_events.append((abs_tick, msg_type, channel,
                                           data1, 0, track_idx))

            # Ensure we're at the end of the track
            f.seek(track_end)

        # --- Resolve tempo map to compute absolute time in seconds ---
        tempo_map.sort(key=lambda x: x[0])

        if bpm_override > 0:
            tempo_map = [(0, int(60_000_000 / bpm_override))]

        result.tempo_bpm = 60_000_000 / tempo_map[0][1]

        def ticks_to_seconds(tick: int) -> float:
            """Convert absolute tick to seconds using tempo map."""
            seconds = 0.0
            prev_tick = 0
            current_uspb = 500000  # default 120 BPM

            for map_tick, uspb in tempo_map:
                if map_tick > tick:
                    break
                elapsed_ticks = min(map_tick, tick) - prev_tick
                seconds += (elapsed_ticks / ticks_per_beat) * (current_uspb / 1_000_000)
                current_uspb = uspb
                prev_tick = map_tick

            remaining = tick - prev_tick
            seconds += (remaining / ticks_per_beat) * (current_uspb / 1_000_000)
            return seconds

        # --- Build note events ---
        # Track note-ons to pair with note-offs
        active_notes = {}  # (channel, note) -> (tick, velocity, track)

        all_events.sort(key=lambda x: x[0])

        for evt in all_events:
            tick, msg_type, channel, data1, data2, track_idx = evt

            if msg_type == 0x9 and data2 > 0:
                # Note on
                key = (channel, data1)
                active_notes[key] = (tick, data2, track_idx)

            elif msg_type == 0x8 or (msg_type == 0x9 and data2 == 0):
                # Note off
                key = (channel, data1)
                if key in active_notes:
                    on_tick, velocity, trk = active_notes.pop(key)
                    result.notes.append(NoteEvent(
                        note=data1,
                        velocity=velocity,
                        time_on=ticks_to_seconds(on_tick),
                        time_off=ticks_to_seconds(tick),
                        channel=channel,
                        track=trk,
                    ))

        # Close any remaining active notes at the last event tick
        if all_events:
            last_tick = all_events[-1][0]
            for key, (on_tick, velocity, trk) in active_notes.items():
                channel, note = key
                result.notes.append(NoteEvent(
                    note=note,
                    velocity=velocity,
                    time_on=ticks_to_seconds(on_tick),
                    time_off=ticks_to_seconds(last_tick),
                    channel=channel,
                    track=trk,
                ))

        result.notes.sort(key=lambda n: n.time_on)

        if result.notes:
            result.duration_seconds = max(n.time_off for n in result.notes)

    return result
