"""
FM-1 (Mini-Tone) parts: generation and encoding.

Pure logic, no device access. `line` makes parts that are not limited by the
TR-8S's Coarse Tune; `minitone` knows the firmware and writes `.mid` / SysEx.
"""

from .line import FORCE_VEL, SYNTH_SHAPES, Part, SynthStep, generate

__all__ = ["FORCE_VEL", "SYNTH_SHAPES", "Part", "SynthStep", "generate"]
