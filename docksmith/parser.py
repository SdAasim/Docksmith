"""
Docksmithfile parser.
Supported: FROM  COPY  RUN  WORKDIR  ENV  CMD
Any other instruction → immediate error with line number.
"""

import json
import sys
from dataclasses import dataclass, field
from typing import List

VALID_INSTRUCTIONS = {"FROM", "COPY", "RUN", "WORKDIR", "ENV", "CMD"}
LAYER_PRODUCING    = {"COPY", "RUN"}


@dataclass
class Instruction:
    lineno: int
    op: str
    raw_args: str

    # FROM
    from_name: str = ""
    from_tag:  str = ""

    # COPY
    copy_srcs: List[str] = field(default_factory=list)
    copy_dest: str = ""

    # RUN
    run_cmd: str = ""

    # WORKDIR
    workdir: str = ""

    # ENV
    env_key: str = ""
    env_val: str = ""

    # CMD
    cmd_list: List[str] = field(default_factory=list)


def parse_docksmithfile(path: str) -> List[Instruction]:
    try:
        raw = open(path).read()
    except FileNotFoundError:
        print(f"Error: Docksmithfile not found at '{path}'", file=sys.stderr)
        sys.exit(1)

    # Resolve backslash continuations, track start line
    logical = []
    buf, start = "", 1
    for lineno, line in enumerate(raw.splitlines(), 1):
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            if not buf:
                start = lineno
            buf += stripped[:-1] + " "
        else:
            logical.append((start if buf else lineno, (buf + stripped).strip()))
            buf = ""
            start = lineno + 1
    if buf:
        logical.append((start, buf.strip()))

    instructions: List[Instruction] = []
    for lineno, line in logical:
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        op    = parts[0].upper()
        args  = parts[1].strip() if len(parts) > 1 else ""

        if op not in VALID_INSTRUCTIONS:
            print(f"Error: unknown instruction '{op}' at line {lineno}", file=sys.stderr)
            sys.exit(1)

        instr = Instruction(lineno=lineno, op=op, raw_args=args)
        _parse_args(instr)
        instructions.append(instr)

    if not instructions or instructions[0].op != "FROM":
        print("Error: Docksmithfile must begin with FROM", file=sys.stderr)
        sys.exit(1)

    return instructions


def _parse_args(i: Instruction):
    if i.op == "FROM":
        ref = i.raw_args
        if ":" in ref:
            i.from_name, i.from_tag = ref.rsplit(":", 1)
        else:
            i.from_name, i.from_tag = ref, "latest"

    elif i.op == "COPY":
        tokens = i.raw_args.split()
        if len(tokens) < 2:
            print(f"Error: COPY needs at least one src and a dest (line {i.lineno})", file=sys.stderr)
            sys.exit(1)
        i.copy_srcs = tokens[:-1]
        i.copy_dest = tokens[-1]

    elif i.op == "RUN":
        i.run_cmd = i.raw_args

    elif i.op == "WORKDIR":
        i.workdir = i.raw_args

    elif i.op == "ENV":
        if "=" in i.raw_args:
            k, v = i.raw_args.split("=", 1)
            i.env_key, i.env_val = k.strip(), v.strip().strip('"').strip("'")
        else:
            parts = i.raw_args.split(None, 1)
            if len(parts) != 2:
                print(f"Error: invalid ENV at line {i.lineno}", file=sys.stderr)
                sys.exit(1)
            i.env_key, i.env_val = parts[0], parts[1].strip('"').strip("'")

    elif i.op == "CMD":
        try:
            lst = json.loads(i.raw_args)
            if not isinstance(lst, list) or not all(isinstance(x, str) for x in lst):
                raise ValueError
            i.cmd_list = lst
        except (json.JSONDecodeError, ValueError):
            print(
                f"Error: CMD must be a JSON string array e.g. [\"sh\",\"-c\",\"echo\"] "
                f"at line {i.lineno}",
                file=sys.stderr,
            )
            sys.exit(1)