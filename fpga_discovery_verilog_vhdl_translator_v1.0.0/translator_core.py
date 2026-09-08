from __future__ import annotations

import re
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class Port:
    direction: str
    name: str
    width: Optional[str] = None
    kind: Optional[str] = None
    initializer: Optional[str] = None
    signed: bool = False


@dataclass
class Signal:
    kind: str
    name: str
    width: Optional[str] = None
    line: Optional[int] = None
    initializer: Optional[str] = None
    signed: bool = False


@dataclass
class Parameter:
    name: str
    value: str
    width: Optional[str] = None


@dataclass
class LocalParam:
    name: str
    value: str
    width: Optional[str] = None


@dataclass
class Memory:
    name: str
    elem_width: Optional[str]
    lo: int
    hi: int
    init_file: Optional[str] = None
    line: Optional[int] = None


@dataclass
class Assignment:
    lhs: str
    rhs: str
    line: Optional[int] = None


@dataclass
class AlwaysBlock:
    sensitivity: str
    body: str
    line: int


@dataclass
class GenerateBlock:
    kind: str  # "for" or "if"
    label: str
    assignments: List[Assignment] = field(default_factory=list)
    genvar: Optional[str] = None
    start_expr: Optional[str] = None
    stop_expr: Optional[str] = None
    condition: Optional[str] = None
    else_label: Optional[str] = None
    else_assignments: List[Assignment] = field(default_factory=list)
    line: Optional[int] = None


@dataclass
class Instance:
    module_name: str
    instance_name: str
    port_map: list[tuple[str, str]] = field(default_factory=list)
    generic_map: list[tuple[str, str]] = field(default_factory=list)
    line: Optional[int] = None


@dataclass
class Subprogram:
    kind: str  # function or task
    name: str
    return_width: Optional[str] = None
    ports: List[Port] = field(default_factory=list)
    locals: List[Signal] = field(default_factory=list)
    integer_locals: List[str] = field(default_factory=list)
    body: str = ""
    line: Optional[int] = None


@dataclass
class Module:
    name: str
    ports: List[Port] = field(default_factory=list)
    parameters: List[Parameter] = field(default_factory=list)
    localparams: List[LocalParam] = field(default_factory=list)
    memories: List[Memory] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)
    continuous_assignments: List[Assignment] = field(default_factory=list)
    always_blocks: List[AlwaysBlock] = field(default_factory=list)
    instances: List[Instance] = field(default_factory=list)
    generate_blocks: List[GenerateBlock] = field(default_factory=list)
    subprograms: List[Subprogram] = field(default_factory=list)
    source_path: Optional[Path] = None


@dataclass
class Diagnostic:
    severity: str
    message: str
    line: Optional[int] = None

    def format(self) -> str:
        loc = f" (line {self.line})" if self.line else ""
        return f"{self.severity.upper()}: {self.message}{loc}"


@dataclass
class TranslationResult:
    module: Optional[Module]
    vhdl: str
    diagnostics: List[Diagnostic]

    @property
    def ok(self) -> bool:
        return self.module is not None and not any(d.severity == "error" for d in self.diagnostics)


class VerilogSubsetParser:
    """Parser for the supported synthesizable Verilog-2001 RTL subset.

    Milestone 5 RTL core includes combinational always @(*), case statements, else-if chains,
    concatenation support in expressions, parameters/generics, continuous assigns,
    and simple named-port module instantiation.
    """

    def parse(self, text: str, source_path: Optional[Path] = None) -> tuple[Optional[Module], List[Diagnostic]]:
        diagnostics: List[Diagnostic] = []
        clean = self._strip_comments(text)

        module_match = re.search(r"\bmodule\s+([A-Za-z_]\w*)\s*(?:#\s*\((.*?)\))?\s*\((.*?)\)\s*;", clean, re.S)
        if not module_match:
            diagnostics.append(Diagnostic("error", "Could not find a supported module declaration."))
            return None, diagnostics

        module = Module(name=module_match.group(1), source_path=source_path)
        module.parameters.extend(self._parse_parameters(module_match.group(2) or "", diagnostics))
        module.ports.extend(self._parse_ansi_ports(module_match.group(3) or "", diagnostics))

        body_start = module_match.end()
        endmodule = re.search(r"\bendmodule\b", clean[body_start:])
        if not endmodule:
            diagnostics.append(Diagnostic("error", "Missing endmodule."))
            return None, diagnostics
        body = clean[body_start: body_start + endmodule.start()]

        # Extract synthesizable Verilog functions/tasks before scanning module-level
        # declarations. Keeping their spans masked prevents helper-body statements
        # from being mistaken for module instances or architecture signals.
        subprogram_spans: list[tuple[int, int]] = []
        sub_re = re.compile(r"(?ms)^\s*(function|task)\b(.*?)(endfunction|endtask)\b")
        for sm in sub_re.finditer(body):
            kind = sm.group(1)
            raw = sm.group(0)
            line = clean[:body_start + sm.start()].count("\n") + 1
            sub = self._parse_subprogram(raw, kind, diagnostics, line)
            if sub is not None:
                module.subprograms.append(sub)
            subprogram_spans.append((sm.start(), sm.end()))

        masked_chars = list(body)
        for s0, e0 in subprogram_spans:
            for i in range(s0, e0):
                if masked_chars[i] != "\n":
                    masked_chars[i] = " "
        scan_body = "".join(masked_chars)

        # Classic/non-ANSI port declarations.
        for m in re.finditer(r"(?m)^\s*(input|output|inout)\s+(?:(wire|reg)\s+)?(?:(signed)\s+)?(\[[^\]]+\]\s+)?([^;]+);", scan_body):
            direction, kind, signed_kw, width, names = m.groups()
            width = width.strip() if width else None
            for raw_name in names.split(','):
                p_name = raw_name.strip()
                if not p_name:
                    continue
                existing = next((p for p in module.ports if p.name == p_name), None)
                if existing:
                    existing.direction, existing.kind, existing.width, existing.signed = direction, kind, width, bool(signed_kw)
                else:
                    module.ports.append(Port(direction, p_name, width, kind, signed=bool(signed_kw)))

        # Standalone module-level integer declarations. These are commonly used
        # as synthesizable procedural scratch values.  Restrict matching to lines
        # that actually begin with `integer` so `parameter integer` and
        # `localparam integer` are not mistaken for scratch-variable declarations.
        for m in re.finditer(r"(?m)^\s*integer\s+([^;]+);", scan_body):
            line = clean[:body_start + m.start()].count("\n") + 1
            for raw_name in self._split_top_level_commas(m.group(1)):
                name = raw_name.strip()
                if re.fullmatch(r"[A-Za-z_]\w*", name):
                    module.signals.append(Signal("integer", name, None, line))
                else:
                    diagnostics.append(Diagnostic("error", f"Could not parse integer declaration: {name}", line))

        # Internal wire/reg declarations, including synthesizable declaration-time
        # initializers and one-dimensional unpacked memories.
        for m in re.finditer(r"\b(wire|reg)\s+(?:(signed)\s+)?(\[[^\]]+\]\s+)?([^;]+);", scan_body):
            kind, signed_kw, width, names = m.groups()
            width = width.strip() if width else None
            line = clean[:body_start + m.start()].count("\n") + 1
            for raw_name in self._split_top_level_commas(names):
                item = raw_name.strip()
                mmem = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*:\s*(\d+)\s*\]", item)
                if mmem and kind == "reg":
                    name, lo, hi = mmem.group(1), int(mmem.group(2)), int(mmem.group(3))
                    module.memories.append(Memory(name, width, lo, hi, line=line))
                    continue
                init = None
                if '=' in item:
                    name, init = item.split('=', 1)
                    name, init = name.strip(), init.strip()
                else:
                    name = item
                if not re.fullmatch(r"[A-Za-z_]\w*", name):
                    diagnostics.append(Diagnostic("error", f"Could not parse internal declaration name: {raw_name.strip()}", line))
                    continue
                module.signals.append(Signal(kind, name, width, line, init if kind == "reg" else None, bool(signed_kw)))
                if init is not None and kind == "wire":
                    module.continuous_assignments.append(Assignment(name, init, line))

        # Recognize synthesizable FPGA initialization forms. ROM $readmemh is
        # embedded into generated VHDL. Simple constant assignments in an initial
        # block are lowered to VHDL declaration initializers, matching the power-up
        # initialization supported by FPGA synthesis tools such as Vivado.
        initial_spans: list[tuple[int,int]] = []
        for im in re.finditer(r"\binitial\s+begin\b", scan_body):
            bstart = im.end()
            bend = self._find_matching_end(scan_body, bstart)
            line = clean[:body_start + im.start()].count("\n") + 1
            if bend is None:
                diagnostics.append(Diagnostic("error", "Unbalanced begin/end in initial block.", line)); continue
            text_i = scan_body[bstart:bend].strip()
            endtok = re.match(r"\bend\b", scan_body[bend:])
            endpos = bend + (endtok.end() if endtok else 3)
            initial_spans.append((im.start(), endpos))

            rm = re.fullmatch(r'\$readmemh\s*\(\s*"([^"]+)"\s*,\s*([A-Za-z_]\w*)\s*\)\s*;', text_i, re.S)
            if rm:
                fname, memname = rm.groups()
                mem = next((x for x in module.memories if x.name == memname), None)
                if not mem:
                    diagnostics.append(Diagnostic("error", f"$readmemh target is not a parsed memory: {memname}", line)); continue
                mem.init_file = fname
                continue

            # Accept one or more plain blocking/nonblocking constant assignments.
            stmts = [x.strip() for x in text_i.split(';') if x.strip()]
            parsed = []
            ok = bool(stmts)
            for stmt in stmts:
                am = re.fullmatch(r"([A-Za-z_]\w*)\s*(?:<=|=)\s*(.+)", stmt, re.S)
                if not am:
                    ok = False; break
                name, rhs = am.group(1), am.group(2).strip()
                # Keep this deliberately deterministic: initial values may be literals,
                # concatenations, or parameter/localparam expressions, but not runtime signals.
                rhs_ids = re.sub(r"\b\d+\s*'[sS]?[bBdDhHoO][0-9a-fA-F_xXzZ_]+", "", rhs)
                ids = set(re.findall(r"\b[A-Za-z_]\w*\b", rhs_ids))
                allowed = {p.name for p in module.parameters} | {lp.name for lp in module.localparams}
                # localparams are parsed later, so also accept names declared by localparam text.
                allowed |= set(re.findall(r"(?m)^\s*localparam\s+(?:integer\s+)?(?:\[[^\]]+\]\s+)?([A-Za-z_]\w*)", scan_body))
                if any(i not in allowed for i in ids):
                    ok = False; break
                parsed.append((name, rhs))
            if not ok:
                diagnostics.append(Diagnostic("error", "Unsupported initial block. Supported forms are $readmemh or simple constant register initialization.", line)); continue
            for name, rhs in parsed:
                target_port = next((x for x in module.ports if x.name == name and x.direction == "output" and x.kind == "reg"), None)
                target_sig = next((x for x in module.signals if x.name == name and x.kind == "reg"), None)
                if target_port is not None:
                    target_port.initializer = rhs
                elif target_sig is not None:
                    target_sig.initializer = rhs
                else:
                    diagnostics.append(Diagnostic("error", f"Initial assignment target is not a parsed reg/output reg: {name}", line))

        # Body parameters are accepted as architecture constants only when they do not
        # duplicate a module-header parameter. For M3 we keep them in parameters and
        # diagnose duplicates; typical channel RTL uses header parameters.
        for m in re.finditer(r"(?m)^\s*parameter\s+([A-Za-z_]\w*)\s*=\s*([^;]+);", scan_body):
            name, value = m.group(1), m.group(2).strip()
            if not any(p.name == name for p in module.parameters):
                module.parameters.append(Parameter(name, value))

        # localparam is internal to the architecture, not an entity generic.
        # Verilog permits one declaration to define several constants that share
        # the same type/width:
        #
        #   localparam [3:0] IDLE = 4'h0,
        #                    RUN  = 4'h1,
        #                    DONE = 4'h2;
        #
        # Parse the whole declaration first, then split declarators at top-level
        # commas so each one becomes an independent VHDL constant declaration.
        for m in re.finditer(
            r"(?ms)^\s*localparam\s+(?:integer\s+)?(\[[^\]]+\]\s+)?(.+?);",
            scan_body
        ):
            width, decls = m.groups()
            common_width = width.strip() if width else None

            for item in self._split_top_level_commas(decls):
                dm = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*=\s*(.+?)\s*", item, re.S)
                if not dm:
                    line = clean[:body_start + m.start()].count("\n") + 1
                    diagnostics.append(Diagnostic(
                        "error",
                        f"Unsupported localparam declarator: {item.strip()}",
                        line,
                    ))
                    continue

                name, value = dm.groups()
                value = value.strip()
                parsed_width = common_width

                # Preserve the self-determined width of a simple sized Verilog
                # literal for otherwise-untyped localparams.
                if parsed_width is None:
                    lm = re.fullmatch(r"(\d+)'(?:[sS])?[bBdDhHoO][0-9a-fA-F_xXzZ_]+", value)
                    if lm:
                        n = int(lm.group(1))
                        parsed_width = f"[{n-1}:0]" if n > 1 else "[0:0]"

                module.localparams.append(
                    LocalParam(name=name, value=value, width=parsed_width)
                )

        # Structural generate blocks: counted generate-for plus generate-if/else.
        # Build 6.50 deliberately supports continuous assignments in generate
        # bodies first. Unsupported structural content is diagnosed explicitly.
        generate_spans: list[tuple[int, int]] = []

        def parse_generate_assigns(text_g: str, base_line: int) -> list[Assignment]:
            assigns: list[Assignment] = []
            masked_g = list(text_g)
            for am in re.finditer(r"\bassign\s+([^=;]+?)\s*=\s*([^;]+);", text_g, re.S):
                line_g = base_line + text_g[:am.start()].count("\n")
                assigns.append(Assignment(am.group(1).strip(), am.group(2).strip(), line_g))
                for j in range(am.start(), am.end()):
                    if masked_g[j] != "\n":
                        masked_g[j] = " "
            leftover = "".join(masked_g)
            leftover = re.sub(r"\bgenvar\s+[A-Za-z_]\w*\s*;", " ", leftover)
            if leftover.strip():
                diagnostics.append(Diagnostic(
                    "error",
                    "Unsupported generate-body content. Build 6.50 supports structural continuous assignments inside generate blocks.",
                    base_line,
                ))
            return assigns

        for gm in re.finditer(r"\bgenerate\b", scan_body):
            if any(s <= gm.start() < e for s, e in generate_spans):
                continue
            eg = re.search(r"\bendgenerate\b", scan_body[gm.end():])
            line_g = clean[:body_start + gm.start()].count("\n") + 1
            if not eg:
                diagnostics.append(Diagnostic("error", "Missing endgenerate.", line_g))
                continue

            g_end = gm.end() + eg.end()
            generate_spans.append((gm.start(), g_end))
            content = scan_body[gm.end(): gm.end() + eg.start()]
            pos = 0

            while pos < len(content):
                ws = re.match(r"\s*", content[pos:], re.S)
                if ws:
                    pos += ws.end()
                if pos >= len(content):
                    break

                gv = re.match(r"genvar\s+[A-Za-z_]\w*\s*;", content[pos:])
                if gv:
                    pos += gv.end()
                    continue

                fm = re.match(
                    r"for\s*\(\s*([A-Za-z_]\w*)\s*=\s*([^;]+?)\s*;\s*"
                    r"\1\s*<\s*([^;]+?)\s*;\s*"
                    r"\1\s*=\s*\1\s*\+\s*1\s*\)\s*"
                    r"begin\s*:\s*([A-Za-z_]\w*)",
                    content[pos:], re.S,
                )
                if fm:
                    var, start_expr, stop_expr, label = fm.groups()
                    body_start_g = pos + fm.end()
                    body_end_g = self._find_matching_end(content, body_start_g)
                    if body_end_g is None:
                        diagnostics.append(Diagnostic("error", "Unbalanced begin/end in generate-for block.", line_g))
                        break
                    block_line = line_g + content[:pos].count("\n")
                    assigns = parse_generate_assigns(content[body_start_g:body_end_g], block_line)
                    module.generate_blocks.append(GenerateBlock(
                        kind="for",
                        label=label,
                        assignments=assigns,
                        genvar=var,
                        start_expr=start_expr.strip(),
                        stop_expr=stop_expr.strip(),
                        line=block_line,
                    ))
                    et = re.match(r"\bend\b", content[body_end_g:])
                    pos = body_end_g + (et.end() if et else 3)
                    continue

                im = re.match(
                    r"if\s*\((.*?)\)\s*begin\s*:\s*([A-Za-z_]\w*)",
                    content[pos:], re.S,
                )
                if im:
                    condition, label = im.groups()
                    body_start_g = pos + im.end()
                    body_end_g = self._find_matching_end(content, body_start_g)
                    if body_end_g is None:
                        diagnostics.append(Diagnostic("error", "Unbalanced begin/end in generate-if block.", line_g))
                        break

                    block_line = line_g + content[:pos].count("\n")
                    assigns = parse_generate_assigns(content[body_start_g:body_end_g], block_line)
                    et = re.match(r"\bend\b", content[body_end_g:])
                    after_end = body_end_g + (et.end() if et else 3)

                    else_label = None
                    else_assigns: list[Assignment] = []
                    em = re.match(r"\s*else\s*begin\s*:\s*([A-Za-z_]\w*)", content[after_end:], re.S)
                    if em:
                        else_label = em.group(1)
                        else_start = after_end + em.end()
                        else_end = self._find_matching_end(content, else_start)
                        if else_end is None:
                            diagnostics.append(Diagnostic("error", "Unbalanced begin/end in generate-if else block.", block_line))
                            break
                        else_assigns = parse_generate_assigns(
                            content[else_start:else_end],
                            block_line + content[pos:else_start].count("\n"),
                        )
                        eet = re.match(r"\bend\b", content[else_end:])
                        pos = else_end + (eet.end() if eet else 3)
                    else:
                        pos = after_end

                    module.generate_blocks.append(GenerateBlock(
                        kind="if",
                        label=label,
                        assignments=assigns,
                        condition=condition.strip(),
                        else_label=else_label,
                        else_assignments=else_assigns,
                        line=block_line,
                    ))
                    continue

                diagnostics.append(Diagnostic(
                    "error",
                    "Unsupported generate form. Build 6.50 supports counted generate-for and generate-if blocks.",
                    line_g + content[:pos].count("\n"),
                ))
                break

        # Mask generate regions so their assignments are not emitted again as
        # ordinary top-level concurrent assignments.
        structural_chars = list(scan_body)
        for s, e in generate_spans:
            for i in range(s, e):
                if structural_chars[i] != "\n":
                    structural_chars[i] = " "
        structural_body = "".join(structural_chars)

        for m in re.finditer(r"\bassign\s+([^=;]+?)\s*=\s*([^;]+);", structural_body, re.S):
            line = clean[:body_start + m.start()].count("\n") + 1
            module.continuous_assignments.append(Assignment(m.group(1).strip(), m.group(2).strip(), line))

        always_spans: list[tuple[int, int]] = []
        # Support always @(*) and always @(posedge clk), with optional spacing.
        for start_match in re.finditer(r"\balways\s*@\s*(?:\(([^)]*)\)|(\*))\s*begin\b", structural_body):
            sensitivity = (start_match.group(1) or start_match.group(2)).strip()
            block_start = start_match.end()
            block_end = self._find_matching_end(structural_body, block_start)
            line = clean[:body_start + start_match.start()].count("\n") + 1
            if block_end is None:
                diagnostics.append(Diagnostic("error", "Unbalanced begin/end in always block.", line))
                continue
            end_token = re.match(r"\bend\b", structural_body[block_end:])
            end_pos = block_end + (end_token.end() if end_token else 3)
            always_spans.append((start_match.start(), end_pos))
            module.always_blocks.append(AlwaysBlock(sensitivity, structural_body[block_start:block_end].strip(), line))

        # Also support an always block whose procedural statement is not wrapped
        # in an outer begin/end.  This includes the very common form:
        #   always @(posedge clk, posedge reset)
        #       if (reset) begin ... end else begin ... end
        # as well as a single assignment.
        always_header_re = re.compile(r"\balways\s*@\s*(?:\(([^)]*)\)|(\*))")
        for sm in always_header_re.finditer(structural_body):
            if any(s0 <= sm.start() < e0 for s0, e0 in always_spans):
                continue
            sensitivity = (sm.group(1) or sm.group(2)).strip()
            stmt_start = sm.end()
            stmt_end = self._find_proc_statement_end(structural_body, stmt_start)
            line = clean[:body_start + sm.start()].count("\n") + 1
            if stmt_end is None:
                diagnostics.append(Diagnostic("error", "Could not parse procedural statement after always sensitivity list.", line))
                continue
            stmt = structural_body[stmt_start:stmt_end].strip()
            always_spans.append((sm.start(), stmt_end))
            module.always_blocks.append(AlwaysBlock(sensitivity, stmt, line))

        # Simple named-port instances. We mask always blocks first so procedural calls
        # cannot be mistaken for instances.
        masked = list(structural_body)
        for s, e in always_spans:
            for i in range(s, e):
                if masked[i] != '\n':
                    masked[i] = ' '
        masked_body = ''.join(masked)
        instance_re = re.compile(
            r"(?ms)^\s*([A-Za-z_]\w*)\s*(?:#\s*\((.*?)\))?\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*;"
        )
        reserved = {"assign", "wire", "reg", "input", "output", "inout", "parameter", "localparam"}
        for m in instance_re.finditer(masked_body):
            mod_name, generic_text, inst_name, port_text = m.groups()
            if mod_name in reserved:
                continue
            line = clean[:body_start + m.start()].count("\n") + 1
            ports = self._parse_named_assoc(port_text, diagnostics, line, "port")
            generics = self._parse_named_assoc(generic_text or "", diagnostics, line, "parameter")
            if ports is not None and generics is not None:
                module.instances.append(Instance(mod_name, inst_name, ports, generics, line))

        unsupported = [
            (r"#\s*\d", "delay controls are not synthesizable and are not supported"),
            (r"\bcasex\b|\bcasez\b", "casex/casez are not supported yet"),
            (r"\bwhile\s*\(", "while loops are not supported yet"),
        ]
        for pat, msg in unsupported:
            for mm in re.finditer(pat, scan_body):
                line = clean[:body_start + mm.start()].count("\n") + 1
                diagnostics.append(Diagnostic("error", msg, line))

        for mm in re.finditer(r"\binitial\b", scan_body):
            if not any(s0 <= mm.start() < e0 for s0,e0 in initial_spans):
                line = clean[:body_start + mm.start()].count("\n") + 1
                diagnostics.append(Diagnostic("error", "Unsupported initial-block form.", line))

        for mm in re.finditer(r"\balways\b", structural_body):
            if not any(s <= mm.start() < e for s, e in always_spans):
                line = clean[:body_start + mm.start()].count("\n") + 1
                diagnostics.append(Diagnostic("error", "Unsupported always-block form. Use always @(...) begin ... end.", line))

        if not module.ports:
            diagnostics.append(Diagnostic("warning", "No ports were parsed."))

        return module, self._dedupe_diagnostics(diagnostics)

    def _parse_subprogram(self, raw: str, kind: str, diagnostics: List[Diagnostic], line: int) -> Optional[Subprogram]:
        """Parse the synthesizable helper-function/task subset used by FPGA RTL."""
        if kind == "function":
            m = re.match(r"(?s)^\s*function\s+(?:(signed)\s+)?(\[[^\]]+\]\s+)?([A-Za-z_]\w*)\s*;(.*)endfunction\b", raw)
            if not m:
                diagnostics.append(Diagnostic("error", "Could not parse function declaration.", line)); return None
            signed_kw, ret_width, name, inner = m.groups()
            sub = Subprogram("function", name, ret_width.strip() if ret_width else None, line=line)
        else:
            m = re.match(r"(?s)^\s*task\s+([A-Za-z_]\w*)\s*;(.*)endtask\b", raw)
            if not m:
                diagnostics.append(Diagnostic("error", "Could not parse task declaration.", line)); return None
            name, inner = m.groups(); sub = Subprogram("task", name, line=line)

        # Declarations precede the begin/end body in the supported helper subset.
        bm = re.search(r"\bbegin\b", inner)
        if not bm:
            diagnostics.append(Diagnostic("error", f"{kind} {name} has no begin/end body.", line)); return None
        decls, rest = inner[:bm.start()], inner[bm.end():]
        bend = self._find_matching_end(inner, bm.end())
        if bend is None:
            diagnostics.append(Diagnostic("error", f"Unbalanced begin/end in {kind} {name}.", line)); return None
        sub.body = inner[bm.end():bend].strip()

        for dm in re.finditer(r"(?m)^\s*(input|output|inout)\s+(?:(wire|reg)\s+)?(?:(signed)\s+)?(\[[^\]]+\]\s+)?([^;]+);", decls):
            direction, pkind, signed_kw, width, names = dm.groups()
            for n in self._split_top_level_commas(names):
                n=n.strip()
                if re.fullmatch(r"[A-Za-z_]\w*", n):
                    sub.ports.append(Port(direction,n,width.strip() if width else None,pkind,signed=bool(signed_kw)))
        for dm in re.finditer(r"(?m)^\s*reg\s+(?:(signed)\s+)?(\[[^\]]+\]\s+)?([^;]+);", decls):
            signed_kw, width, names = dm.groups()
            for n in self._split_top_level_commas(names):
                n=n.strip()
                if re.fullmatch(r"[A-Za-z_]\w*",n): sub.locals.append(Signal("reg",n,width.strip() if width else None,signed=bool(signed_kw)))
        for dm in re.finditer(r"(?m)^\s*integer\s+([A-Za-z_]\w*)\s*;", decls):
            sub.integer_locals.append(dm.group(1))
        return sub

    def _parse_named_assoc(self, text: str, diagnostics: List[Diagnostic], line: int, kind: str) -> Optional[list[tuple[str, str]]]:
        if not text.strip():
            return []
        out: list[tuple[str, str]] = []
        for item in self._split_top_level_commas(text):
            item = item.strip()
            m = re.fullmatch(r"\.([A-Za-z_]\w*)\s*\((.*)\)", item, re.S)
            if not m:
                diagnostics.append(Diagnostic("error", f"Only named {kind} associations are supported in module instantiation: {item}", line))
                return None
            out.append((m.group(1), m.group(2).strip()))
        return out

    def _parse_parameters(self, block: str, diagnostics: List[Diagnostic]) -> List[Parameter]:
        out: List[Parameter] = []
        if not block.strip():
            return out
        for item in self._split_top_level_commas(block):
            item = re.sub(r"^\s*parameter\s+", "", item.strip())
            m = re.match(
                r"(?:(integer)\s+)?(?:(\[[^\]]+\])\s+)?([A-Za-z_]\w*)\s*=\s*(.+)$",
                item, re.S
            )
            if m:
                integer_kw, width, name, value = m.groups()
                if integer_kw and width:
                    diagnostics.append(Diagnostic("error", f"Parameter cannot be both integer and packed-vector in supported subset: {item.strip()}"))
                    continue
                out.append(Parameter(name, value.strip(), width.strip() if width else None))
            else:
                diagnostics.append(Diagnostic("error", f"Could not parse parameter: {item.strip()}"))
        return out

    def _parse_ansi_ports(self, block: str, diagnostics: List[Diagnostic]) -> List[Port]:
        ports: List[Port] = []
        current_direction = current_kind = current_width = None
        for item in self._split_top_level_commas(block):
            item = item.strip()
            if not item:
                continue
            m = re.match(r"(input|output|inout)\s+(?:(wire|reg)\s+)?(?:(signed)\s+)?(\[[^\]]+\]\s+)?([A-Za-z_]\w*)(?:\s*=\s*(.+))?$", item, re.S)
            if m:
                current_direction, current_kind, signed_kw, current_width, name, init = m.groups()
                current_signed = bool(signed_kw)
                ports.append(Port(current_direction, name, current_width.strip() if current_width else None, current_kind, init.strip() if init else None, current_signed))
                continue
            m2 = re.match(r"([A-Za-z_]\w*)(?:\s*=\s*(.+))?$", item, re.S)
            if m2 and current_direction:
                name, init = m2.groups()
                ports.append(Port(current_direction, name, current_width, current_kind, init.strip() if init else None, locals().get("current_signed", False)))
                continue
            if re.match(r"^[A-Za-z_]\w*$", item):
                ports.append(Port("unknown", item))
            else:
                diagnostics.append(Diagnostic("error", f"Could not fully parse port declaration: {item}"))
        return ports

    @staticmethod
    def _split_top_level_commas(text: str) -> List[str]:
        items, buf, depth = [], [], 0
        for ch in text:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            if ch == ',' and depth == 0:
                items.append(''.join(buf)); buf = []
            else:
                buf.append(ch)
        if buf:
            items.append(''.join(buf))
        return items

    @staticmethod
    def _find_proc_statement_end(text: str, start: int) -> Optional[int]:
        """Return the end offset of one Verilog procedural statement.

        Used to capture always bodies that omit an outer begin/end wrapper.
        Handles begin/end, if/else chains, case/endcase, and ordinary
        semicolon-terminated statements. Comments have already been masked.
        """
        n = len(text)

        def skip_ws(i: int) -> int:
            while i < n and text[i].isspace():
                i += 1
            return i

        def word_at(i: int, word: str) -> bool:
            if not text.startswith(word, i):
                return False
            before_ok = i == 0 or not (text[i-1].isalnum() or text[i-1] == '_')
            j = i + len(word)
            after_ok = j >= n or not (text[j].isalnum() or text[j] == '_')
            return before_ok and after_ok

        def balanced_close(i: int, open_ch='(', close_ch=')') -> Optional[int]:
            if i >= n or text[i] != open_ch:
                return None
            depth = 1
            i += 1
            while i < n:
                if text[i] == open_ch:
                    depth += 1
                elif text[i] == close_ch:
                    depth -= 1
                    if depth == 0:
                        return i + 1
                i += 1
            return None

        def parse_stmt(i: int) -> Optional[int]:
            i = skip_ws(i)
            if i >= n:
                return None

            if word_at(i, 'begin'):
                inner = i + len('begin')
                end_pos = VerilogSubsetParser._find_matching_end(text, inner)
                if end_pos is None:
                    return None
                return end_pos + len('end')

            if word_at(i, 'if'):
                j = skip_ws(i + len('if'))
                j = balanced_close(j)
                if j is None:
                    return None
                j = parse_stmt(j)
                if j is None:
                    return None
                k = skip_ws(j)
                if word_at(k, 'else'):
                    return parse_stmt(k + len('else'))
                return j

            if word_at(i, 'case'):
                # Find the matching endcase. Nested case statements are uncommon
                # here but are handled with a depth counter.
                depth = 0
                for m in re.finditer(r"\b(case|endcase)\b", text[i:]):
                    depth += 1 if m.group(1) == 'case' else -1
                    if depth == 0:
                        return i + m.end()
                return None

            depth = 0
            j = i
            while j < n:
                ch = text[j]
                if ch in '([{':
                    depth += 1
                elif ch in ')]}':
                    depth = max(0, depth - 1)
                elif ch == ';' and depth == 0:
                    return j + 1
                j += 1
            return None

        return parse_stmt(start)

    @staticmethod
    def _find_matching_end(text: str, start: int) -> Optional[int]:
        depth = 1
        for m in re.finditer(r"\b(begin|end)\b", text[start:]):
            depth += 1 if m.group(1) == "begin" else -1
            if depth == 0:
                return start + m.start()
        return None

    @staticmethod
    def _strip_comments(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
        return re.sub(r"//.*", "", text)

    @staticmethod
    def _dedupe_diagnostics(items: List[Diagnostic]) -> List[Diagnostic]:
        out, seen = [], set()
        for d in items:
            key = (d.severity, d.message, d.line)
            if key not in seen:
                seen.add(key); out.append(d)
        return out


class VHDLGenerator:
    _XPM_COMPONENTS = {"xpm_memory_sprom"}
    _UNISIM_COMPONENTS = {"STARTUPE2"}
    # VHDL reserved words cannot be used as ordinary identifiers. Verilog has
    # a different keyword set, so legal Verilog names such as "select" need
    # VHDL extended-identifier escaping when emitted.
    _VHDL_RESERVED = {
        "abs","access","after","alias","all","and","architecture","array",
        "assert","attribute","begin","block","body","buffer","bus","case",
        "component","configuration","constant","disconnect","downto","else",
        "elsif","end","entity","exit","file","for","function","generate",
        "generic","group","guarded","if","impure","in","inertial","inout",
        "is","label","library","linkage","literal","loop","map","mod","nand",
        "new","next","nor","not","null","of","on","open","or","others","out",
        "package","port","postponed","procedure","process","pure","range",
        "record","register","reject","rem","report","return","rol","ror",
        "select","severity","shared","signal","sla","sll","sra","srl",
        "subtype","then","to","transport","type","unaffected","units","until",
        "use","variable","wait","when","while","with","xnor","xor"
    }

    @classmethod
    def _vhdl_ident(cls, name: str) -> str:
        return f"\\{name}\\" if name.lower() in cls._VHDL_RESERVED else name

    def _source_ident(self, name: str) -> str:
        mapped = getattr(self, "_source_name_map", {}).get(name, name)
        return self._vhdl_ident(mapped)

    @staticmethod
    def _wire_output_proxies(module: Module) -> list[Port]:
        """Output-wire ports that are read internally need an architecture signal.

        Reading an OUT port requires VHDL-2008; using a proxy keeps generated code
        compatible with the normal Vivado VHDL flow as well.
        """
        out = []
        for p in module.ports:
            if p.direction != "output" or p.kind == "reg":
                continue

            pat = rf"\b{re.escape(p.name)}\b"

            # Ordinary procedural and continuous reads.
            read = any(re.search(pat, b.body) for b in module.always_blocks)
            if not read:
                read = any(re.search(pat, a.rhs) for a in module.continuous_assignments)

            # Generate blocks are elaborated into concurrent VHDL statements.
            # A Verilog output wire may legally feed logic inside a generate
            # block, but reading a VHDL OUT port would require VHDL-2008.
            # Detect those reads too so the output is routed through the same
            # architecture-local proxy used elsewhere.
            if not read:
                for gb in module.generate_blocks:
                    if any(re.search(pat, a.rhs) for a in gb.assignments):
                        read = True
                        break
                    if any(re.search(pat, a.rhs) for a in gb.else_assignments):
                        read = True
                        break

            if read:
                out.append(p)
        return out

    def generate(self, module: Module, diagnostics: List[Diagnostic], module_interfaces: Optional[dict[str, Module]] = None) -> str:
        reg_ports = [p for p in module.ports if p.direction == "output" and p.kind == "reg"]
        wire_proxy_ports = self._wire_output_proxies(module)

        self._signed_names = {p.name for p in module.ports if p.signed}
        self._signed_names.update(sig.name for sig in module.signals if sig.signed)
        for p in reg_ports:
            if p.signed:
                self._signed_names.add(p.name + "_reg")
        for p in wire_proxy_ports:
            if p.signed:
                self._signed_names.add(p.name + "_int")

        signal_widths = self._build_width_map(module, reg_ports)
        for p in wire_proxy_ports:
            signal_widths[p.name + "_int"] = self._constant_width(p.width)

        # Only real VHDL signals may appear in a process sensitivity list.
        # Sized localparams/constants participate in expression typing and are
        # therefore present in signal_widths, but they are static constants,
        # not signals. Keep a separate set for combinational sensitivity
        # inference so constants such as C_BLACK never leak into process(...).
        sensitivity_signals = {p.name for p in module.ports}
        sensitivity_signals.update(s.name for s in module.signals if s.kind != "integer")
        sensitivity_signals.update(m.name for m in module.memories)
        sensitivity_signals.update(p.name + "_reg" for p in reg_ports)
        sensitivity_signals.update(p.name + "_int" for p in wire_proxy_ports)
        # Untyped Verilog parameters/localparams are modeled as VHDL integers.
        # Keep this set while generating so expression translation can distinguish
        # integer constant arithmetic from std_logic_vector arithmetic.
        self._integer_names = {p.name for p in module.parameters if p.width is None}
        self._integer_names.update(lp.name for lp in module.localparams if lp.width is None)
        self._integer_signal_names = {s.name for s in module.signals if s.kind == "integer"}
        self._integer_names.update(self._integer_signal_names)

        self._comb_integer_vars = set()
        for blk in module.always_blocks:
            if blk.sensitivity == "*":
                for name in self._integer_signal_names:
                    if re.search(rf"\b{re.escape(name)}\b", blk.body):
                        self._comb_integer_vars.add(name)
        self._task_names = {sp.name for sp in module.subprograms if sp.kind == "task"}
        self._subprogram_map = {sp.name: sp for sp in module.subprograms}
        self._module_signal_map = {sig.name: sig for sig in module.signals}
        self._module_port_map = {p.name: p for p in module.ports}

        self._task_outer_writes = {}
        for sp in module.subprograms:
            if sp.kind != "task":
                continue
            local_names = {p.name for p in sp.ports} | {v.name for v in sp.locals}
            writes = []
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?:<=|=(?!=))", sp.body):
                name = m.group(1)
                if name in local_names or name in writes:
                    continue
                if name in self._module_port_map or name in self._module_signal_map:
                    writes.append(name)
            self._task_outer_writes[sp.name] = writes

        # Verilog identifiers are case-sensitive; VHDL identifiers are not.
        # If an architecture-local constant differs from another declaration only
        # by case (Frogger has SNAKE_Y and output snake_y), rename the local VHDL
        # constant deterministically and rewrite references to it.
        occupied_ci = {
            x.name.lower()
            for x in (module.ports + module.signals + module.parameters)
        }
        occupied_ci.update(m.name.lower() for m in module.memories)
        self._source_name_map = {}
        used_ci = set(occupied_ci)
        for lp in module.localparams:
            emitted = lp.name
            if lp.name.lower() in used_ci:
                base = f"{lp.name}_const"
                emitted = base
                n = 2
                while emitted.lower() in used_ci:
                    emitted = f"{base}_{n}"
                    n += 1
                self._source_name_map[lp.name] = emitted
            used_ci.add(emitted.lower())

        # Unpacked Verilog memories are VHDL arrays. Keep each element width so
        # expressions such as {data_bytes[1], data_bytes[0]} retain their packed
        # width after memory reads are rewritten as data_bytes(1).
        self._memory_widths = {m.name: self._constant_width(m.elem_width) for m in module.memories}

        # VHDL-93 allows expressions as input port actuals, but Vivado warns when
        # an actual is not a static name/globally static expression.  Preserve the
        # Verilog connectivity while keeping generated projects clean by routing
        # complex input expressions through architecture-local helper signals.
        port_actual_helpers: dict[tuple[str, str], tuple[str, str, Optional[int]]] = {}
        if module_interfaces:
            for inst in module.instances:
                child = module_interfaces.get(inst.module_name)
                if child is None:
                    continue
                child_ports = {p.name: p for p in child.ports}
                for formal, actual in inst.port_map:
                    fp = child_ports.get(formal)
                    if fp is None or fp.direction != "input":
                        continue
                    raw = actual.strip()
                    simple = (
                        re.fullmatch(r"[A-Za-z_]\w*", raw)
                        or re.fullmatch(r"[A-Za-z_]\w*\s*\[[^\]]+\]", raw)
                        or re.fullmatch(r"\d+'(?:[sS])?[bBdDhHoO][0-9a-fA-F_xXzZ_]+", raw)
                        or re.fullmatch(r"\d[\d_]*", raw)
                    )
                    if simple:
                        continue
                    helper = re.sub(r"\W+", "_", f"{inst.instance_name}_{formal}_actual")
                    port_actual_helpers[(inst.instance_name, formal)] = (
                        helper,
                        raw,
                        self._constant_width(fp.width),
                    )
                    signal_widths[helper] = self._constant_width(fp.width)

        lines: List[str] = [
            "library ieee;",
            "use ieee.std_logic_1164.all;",
            "use ieee.numeric_std.all;",
        ]
        if any(inst.module_name in self._XPM_COMPONENTS for inst in module.instances):
            lines.extend([
                "library xpm;",
                "use xpm.vcomponents.all;",
            ])
        if any(inst.module_name in self._UNISIM_COMPONENTS for inst in module.instances):
            lines.extend([
                "library unisim;",
                "use unisim.vcomponents.all;",
            ])
        lines.extend([
            "",
            f"entity {self._vhdl_ident(module.name)} is",
        ])

        if module.parameters:
            lines.append("    generic (")
            for i, p in enumerate(module.parameters):
                sep = ";" if i < len(module.parameters) - 1 else ""
                if p.width:
                    p_type = self._type_from_width(p.width, p.name, diagnostics)
                    p_value = self._initializer_expr(p.value, p.width, diagnostics, signal_widths)
                    lines.append(f"        {self._vhdl_ident(p.name)} : {p_type} := {p_value}{sep}")
                else:
                    lines.append(f"        {self._vhdl_ident(p.name)} : integer := {self._expr(p.value, diagnostics, signal_widths)}{sep}")
            lines.append("    );")

        if module.ports:
            lines.append("    port (")
            for i, p in enumerate(module.ports):
                sep = ";" if i < len(module.ports) - 1 else ""
                direction = {"input": "in", "output": "out", "inout": "inout"}.get(p.direction)
                if direction is None:
                    diagnostics.append(Diagnostic("error", f"Unknown direction for port {p.name}."))
                    direction = "in"
                lines.append(f"        {self._vhdl_ident(p.name)} : {direction} {self._type_from_width(p.width, p.name, diagnostics)}{sep}")
            lines.append("    );")

        lines.extend([f"end entity {self._vhdl_ident(module.name)};", "", f"architecture rtl of {self._vhdl_ident(module.name)} is"])

        # Pre-scan expression-bearing source regions so helper functions are
        # emitted before architecture BEGIN only when needed.
        expr_corpus_parts: list[str] = []
        expr_corpus_parts.extend(a.rhs for a in module.continuous_assignments)
        expr_corpus_parts.extend(b.body for b in module.always_blocks)
        expr_corpus_parts.extend(lp.value for lp in module.localparams)
        expr_corpus_parts.extend(p.value for p in module.parameters)
        expr_corpus_parts.extend(sp.body for sp in module.subprograms)
        for inst in module.instances:
            expr_corpus_parts.extend(actual for _formal, actual in inst.port_map)
            expr_corpus_parts.extend(actual for _formal, actual in inst.generic_map)
        for gb in module.generate_blocks:
            expr_corpus_parts.extend(a.rhs for a in gb.assignments)
            expr_corpus_parts.extend(a.rhs for a in gb.else_assignments)
        expr_corpus = "\n".join(expr_corpus_parts)

        uses_reduce_and = bool(re.search(r"(?m)(?:^|[=(,:?])\s*&\s*[A-Za-z_({$]", expr_corpus))
        uses_reduce_or  = bool(re.search(r"(?m)(?:^|[=(,:?])\s*\|\s*[A-Za-z_({$]", expr_corpus))
        uses_reduce_xor = bool(re.search(r"(?m)(?:^|[=(,:?])\s*\^\s*[A-Za-z_({$]", expr_corpus))
        uses_repeat = bool(re.search(r"\{\s*(?:[A-Za-z_]\w*|\d+)\s*\{", expr_corpus))

        if uses_reduce_and:
            lines.extend([
                "    function reduce_and(v : std_logic_vector) return std_logic is",
                "        variable r : std_logic := '1';",
                "    begin",
                "        for i in v'range loop",
                "            r := r and v(i);",
                "        end loop;",
                "        return r;",
                "    end function;",
                ""
            ])
        if uses_reduce_or:
            lines.extend([
                "    function reduce_or(v : std_logic_vector) return std_logic is",
                "        variable r : std_logic := '0';",
                "    begin",
                "        for i in v'range loop",
                "            r := r or v(i);",
                "        end loop;",
                "        return r;",
                "    end function;",
                ""
            ])
        if uses_reduce_xor:
            lines.extend([
                "    function reduce_xor(v : std_logic_vector) return std_logic is",
                "        variable r : std_logic := '0';",
                "    begin",
                "        for i in v'range loop",
                "            r := r xor v(i);",
                "        end loop;",
                "        return r;",
                "    end function;",
                ""
            ])
        if uses_repeat:
            lines.extend([
                "    function repeat_slv(value : std_logic_vector; count : natural) return std_logic_vector is",
                "        variable result_v : std_logic_vector(value'length * count - 1 downto 0);",
                "    begin",
                "        for i in 0 to count - 1 loop",
                "            result_v((i + 1) * value'length - 1 downto i * value'length) := value;",
                "        end loop;",
                "        return result_v;",
                "    end function;",
                "",
                "    function repeat_slv(value : std_logic; count : natural) return std_logic_vector is",
                "        variable result_v : std_logic_vector(count - 1 downto 0);",
                "    begin",
                "        result_v := (others => value);",
                "        return result_v;",
                "    end function;",
                ""
            ])

        # Verilog $clog2() is commonly used in parameterized counter widths.
        # Provide a local synthesizable VHDL equivalent whenever a declaration
        # width in this module uses it.
        widths_using_clog2 = [x.width for x in module.ports + module.signals if getattr(x, "width", None) and "$clog2" in x.width]
        widths_using_clog2 += [m.elem_width for m in module.memories if m.elem_width and "$clog2" in m.elem_width]
        values_using_clog2 = [lp.value for lp in module.localparams if "$clog2" in lp.value]
        values_using_clog2 += [p.value for p in module.parameters if "$clog2" in p.value]
        if widths_using_clog2 or values_using_clog2:
            lines.extend([
                "    function clog2(n : positive) return natural is",
                "        variable v : natural := n - 1;",
                "        variable r : natural := 0;",
                "    begin",
                "        while v > 0 loop",
                "            v := v / 2;",
                "            r := r + 1;",
                "        end loop;",
                "        return r;",
                "    end function;",
                ""
            ])
        for lp in module.localparams:
            lp_type = self._type_from_width(lp.width, lp.name, diagnostics) if lp.width else "integer"
            if lp.width:
                lp_val = self._initializer_expr(lp.value, lp.width, diagnostics, signal_widths)
            else:
                # Untyped localparams are emitted as VHDL INTEGERs. Sized Verilog
                # literals still carry a bit width in Verilog, but their numeric
                # value is what an INTEGER constant needs in VHDL.
                literal_integer = self._sized_literal_integer(lp.value)
                if literal_integer is not None:
                    lp_val = literal_integer
                elif self._is_integer_expr(lp.value):
                    lp_val = self._integer_expr(lp.value, diagnostics, signal_widths)
                else:
                    lp_val = self._expr(lp.value, diagnostics, signal_widths)
            lines.append(f"    constant {self._source_ident(lp.name)} : {lp_type} := {lp_val};")
        memory_names = {m.name for m in module.memories}
        for mem in module.memories:
            elem_t = self._type_from_width(mem.elem_width, mem.name, diagnostics)
            tname = f"{mem.name}_array_t"
            lines.append(f"    type {tname} is array ({mem.lo} to {mem.hi}) of {elem_t};")
            if mem.init_file:
                vals = self._load_mem_values(module, mem, diagnostics)
                if vals is not None:
                    lines.append(f"    constant {mem.name} : {tname} := (")
                    for idx, val in enumerate(vals):
                        sep = "," if idx < len(vals)-1 else ""
                        lines.append(f"        {mem.lo+idx} => {val}{sep}")
                    lines.append("    );")
                else:
                    lines.append(f"    signal {mem.name} : {tname};")
            else:
                lines.append(f"    signal {mem.name} : {tname};")
        for sig in module.signals:
            if sig.kind == "integer":
                if sig.name in getattr(self, "_comb_integer_vars", set()):
                    continue
                decl = f"    signal {sig.name} : integer"
                if sig.initializer is not None:
                    decl += f" := {self._integer_expr(sig.initializer, diagnostics, signal_widths)}"
                lines.append(decl + ";")
                continue
            decl = f"    signal {sig.name} : {self._type_from_width(sig.width, sig.name, diagnostics)}"
            if sig.initializer is not None:
                if self._has_vector_width(sig.width) and self._constant_width(sig.width) is None and re.fullmatch(r"\s*(?:\d+'[dDbBhHoO])?0\s*", sig.initializer):
                    init_vhdl = "(others => '0')"
                else:
                    init_vhdl = self._initializer_expr(sig.initializer, sig.width, diagnostics, signal_widths)
                decl += f" := {init_vhdl}"
            lines.append(decl + ";")
        for p in reg_ports:
            decl = f"    signal {p.name}_reg : {self._type_from_width(p.width, p.name, diagnostics)}"
            if p.initializer is not None:
                decl += f" := {self._initializer_expr(p.initializer, p.width, diagnostics, signal_widths)}"
            lines.append(decl + ";")
        for p in wire_proxy_ports:
            lines.append(f"    signal {p.name}_int : {self._type_from_width(p.width, p.name, diagnostics)};")
        for (_inst_name, _formal), (helper, _raw, width) in port_actual_helpers.items():
            if width is None:
                helper_type = "std_logic"
            elif width == 1:
                helper_type = "std_logic"
            else:
                helper_type = f"std_logic_vector({width-1} downto 0)"
            lines.append(f"    signal {helper} : {helper_type};")
        if module.subprograms:
            lines.append("")
            for sp in module.subprograms:
                lines.extend(self._generate_subprogram(sp, diagnostics, signal_widths))
                lines.append("")
        lines.extend(["begin", ""])

        for (_inst_name, _formal), (helper, raw_actual, target_width) in port_actual_helpers.items():
            mapped_actual = self._map_rhs(raw_actual, reg_ports, wire_proxy_ports)
            actual_vhdl = self._assignment_rhs(
                mapped_actual, target_width, diagnostics, signal_widths, helper
            )
            lines.append(f"    {helper} <= {actual_vhdl};")
        if port_actual_helpers:
            lines.append("")

        for a in module.continuous_assignments:
            lhs = self._map_lhs(self._translate_selects(a.lhs, signal_widths), reg_ports, wire_proxy_ports)
            rhs_raw = self._map_rhs(a.rhs, reg_ports, wire_proxy_ports)
            rhs_raw = self._translate_memory_reads(rhs_raw, module)
            target_width = self._lhs_width(lhs, signal_widths)
            base_target = re.match(r"([A-Za-z_]\w*)", lhs)
            target_name = base_target.group(1) if base_target else None
            rhs = self._concurrent_assignment_rhs(
                rhs_raw, target_width, diagnostics, signal_widths, target_name
            )
            lines.append(f"    {lhs} <= {rhs};")
        if module.continuous_assignments:
            lines.append("")

        for gb in module.generate_blocks:
            if gb.kind == "for":
                old_locals = set(getattr(self, "_integer_local_names", set()))
                self._integer_local_names = old_locals | ({gb.genvar} if gb.genvar else set())
                try:
                    start_vhdl = self._integer_expr(gb.start_expr or "0", diagnostics, signal_widths)
                    stop_vhdl = self._integer_expr(gb.stop_expr or "0", diagnostics, signal_widths)
                    lines.append(f"    {gb.label} : for {gb.genvar} in {start_vhdl} to {stop_vhdl} - 1 generate")
                    lines.append("    begin")
                    for ga in gb.assignments:
                        lhs0 = self._translate_selects(ga.lhs, signal_widths)
                        lhs = self._map_lhs(lhs0, reg_ports, wire_proxy_ports)
                        rhs_raw = self._map_rhs(ga.rhs, reg_ports, wire_proxy_ports)
                        target_width = self._lhs_width(lhs, signal_widths)
                        base_target = re.match(r"([A-Za-z_]\w*)", lhs)
                        target_name = base_target.group(1) if base_target else None
                        rhs = self._concurrent_assignment_rhs(
                            rhs_raw, target_width, diagnostics, signal_widths, target_name
                        )
                        lines.append(f"        {lhs} <= {rhs};")
                    lines.append(f"    end generate {gb.label};")
                    lines.append("")
                finally:
                    self._integer_local_names = old_locals

            elif gb.kind == "if":
                cond = self._condition(gb.condition or "0", diagnostics, signal_widths)
                lines.append(f"    {gb.label} : if {cond} generate")
                lines.append("    begin")
                for ga in gb.assignments:
                    lhs0 = self._translate_selects(ga.lhs, signal_widths)
                    lhs = self._map_lhs(lhs0, reg_ports, wire_proxy_ports)
                    rhs_raw = self._map_rhs(ga.rhs, reg_ports, wire_proxy_ports)
                    target_width = self._lhs_width(lhs, signal_widths)
                    rhs = self._assignment_rhs(rhs_raw, target_width, diagnostics, signal_widths)
                    lines.append(f"        {lhs} <= {rhs};")
                lines.append(f"    end generate {gb.label};")
                lines.append("")

                if gb.else_label:
                    lines.append(f"    {gb.else_label} : if not ({cond}) generate")
                    lines.append("    begin")
                    for ga in gb.else_assignments:
                        lhs0 = self._translate_selects(ga.lhs, signal_widths)
                        lhs = self._map_lhs(lhs0, reg_ports, wire_proxy_ports)
                        rhs_raw = self._map_rhs(ga.rhs, reg_ports, wire_proxy_ports)
                        target_width = self._lhs_width(lhs, signal_widths)
                        base_target = re.match(r"([A-Za-z_]\w*)", lhs)
                        target_name = base_target.group(1) if base_target else None
                        rhs = self._concurrent_assignment_rhs(
                            rhs_raw, target_width, diagnostics, signal_widths, target_name
                        )
                        lines.append(f"        {lhs} <= {rhs};")
                    lines.append(f"    end generate {gb.else_label};")
                    lines.append("")

        for inst in module.instances:
            is_xpm = inst.module_name in self._XPM_COMPONENTS
            is_unisim = inst.module_name in self._UNISIM_COMPONENTS
            if is_xpm or is_unisim:
                # Vendor libraries provide VHDL component declarations.
                lines.append(f"    {self._vhdl_ident(inst.instance_name)} : {self._vhdl_ident(inst.module_name)}")
            else:
                lines.append(f"    {self._vhdl_ident(inst.instance_name)} : entity work.{self._vhdl_ident(inst.module_name)}")

            if inst.generic_map:
                lines.append("        generic map (")
                for i, (formal, actual) in enumerate(inst.generic_map):
                    sep = "," if i < len(inst.generic_map) - 1 else ""
                    raw_actual = actual.strip()
                    if (is_xpm or is_unisim) and re.fullmatch(r'"[^"]*"', raw_actual, re.S):
                        # Vendor string generics are VHDL strings.
                        actual_vhdl = raw_actual
                    elif is_unisim and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw_actual):
                        # UNISIM primitives such as STARTUPE2 use REAL generics
                        # (SIM_CCLK_FREQ => 0.0). Preserve the numeric literal.
                        actual_vhdl = raw_actual
                    else:
                        actual_vhdl = self._expr(actual, diagnostics, signal_widths)
                    lines.append(f"            {self._vhdl_ident(formal)} => {actual_vhdl}{sep}")
                lines.append("        )")

            lines.append("        port map (")
            child = module_interfaces.get(inst.module_name) if module_interfaces else None
            child_ports = {p.name: p for p in child.ports} if child else {}
            for i, (formal, actual) in enumerate(inst.port_map):
                sep = "," if i < len(inst.port_map) - 1 else ""
                raw_actual = actual.strip()

                # Verilog empty named-port connections, e.g. .busy(), mean the
                # port is intentionally left unconnected. For a known output
                # port, VHDL spells this OPEN. Vendor primitives retain the same
                # behavior because their formal directions are library-defined.
                formal_port = child_ports.get(formal)
                if raw_actual == "" and (
                    is_xpm
                    or is_unisim
                    or formal_port is None
                    or formal_port.direction in ("output", "inout")
                ):
                    actual_vhdl = "open"
                else:
                    mapped_actual = self._map_rhs(actual, reg_ports, wire_proxy_ports)
                    helper_entry = port_actual_helpers.get((inst.instance_name, formal))
                    if helper_entry is not None:
                        actual_vhdl = helper_entry[0]
                    # Verilog coerces actual expressions to the formal input port width.
                    # VHDL port associations require the types to match explicitly.
                    elif formal_port is not None and formal_port.direction == "input":
                        target_width = self._constant_width(formal_port.width)
                        actual_vhdl = self._assignment_rhs(mapped_actual, target_width, diagnostics, signal_widths)
                    else:
                        actual_vhdl = self._expr(mapped_actual, diagnostics, signal_widths)

                lines.append(f"            {self._vhdl_ident(formal)} => {actual_vhdl}{sep}")
            lines.append("        );")
            lines.append("")

        for block in module.always_blocks:
            self._generate_always(block, lines, diagnostics, reg_ports, wire_proxy_ports, signal_widths, sensitivity_signals, module.subprograms)
            lines.append("")

        for p in reg_ports:
            lines.append(f"    {self._vhdl_ident(p.name)} <= {p.name}_reg;")
        for p in wire_proxy_ports:
            lines.append(f"    {self._vhdl_ident(p.name)} <= {p.name}_int;")

        lines.extend(["", "end architecture rtl;", ""])
        return "\n".join(lines)

    def _generate_subprogram(self, sp: Subprogram, diagnostics: List[Diagnostic], outer_widths: dict[str, Optional[int]]) -> List[str]:
        widths = dict(outer_widths)
        for p in sp.ports: widths[p.name] = self._constant_width(p.width)
        for v in sp.locals: widths[v.name] = self._constant_width(v.width)
        widths[sp.name] = self._constant_width(sp.return_width)

        saved_signed = set(getattr(self, "_signed_names", set()))
        self._signed_names.update(p.name for p in sp.ports if p.signed)
        self._signed_names.update(v.name for v in sp.locals if v.signed)

        out: List[str] = []
        if sp.kind == "function":
            ret_t = self._type_from_width(sp.return_width, sp.name, diagnostics)
            # A VHDL function return indication is a type mark. A constrained
            # std_logic_vector(...) is a subtype indication and Vivado rejects it
            # here as an indexed name. Return the unconstrained base type while
            # keeping the local result variable constrained to the Verilog width.
            ret_mark = "std_logic_vector" if self._constant_width(sp.return_width) not in (None, 1) else ret_t
            args=[]
            for p in sp.ports:
                args.append(f"{p.name} : {self._type_from_width(p.width,p.name,diagnostics)}")
            out.append(f"    impure function {sp.name}({'; '.join(args)}) return {ret_mark} is")
            out.append(f"        variable {sp.name}_result : {ret_t};")
            for v in sp.locals:
                out.append(f"        variable {v.name} : {self._type_from_width(v.width,v.name,diagnostics)};")
            out.append("    begin")
            hp=_HelperProceduralTranslator(self,sp.body,diagnostics,widths,sp.line or 1,sp,False)
            for ln in hp.parse_block(set()): out.append("        "+ln)
            out.append(f"        return {sp.name}_result;")
            out.append(f"    end function {sp.name};")
        else:
            args=[]
            for p in sp.ports:
                mode={'input':'in','output':'out','inout':'inout'}[p.direction]
                cls='signal ' if p.direction in ('output','inout') else ''
                args.append(f"{cls}{p.name} : {mode} {self._type_from_width(p.width,p.name,diagnostics)}")

            outer_map = {}
            for name in getattr(self, "_task_outer_writes", {}).get(sp.name, []):
                formal = f"outer_{name}"
                outer_map[name] = formal
                if name in getattr(self, "_module_port_map", {}):
                    obj = self._module_port_map[name]
                    typ = self._type_from_width(obj.width, name, diagnostics)
                else:
                    obj = self._module_signal_map[name]
                    typ = "integer" if obj.kind == "integer" else self._type_from_width(obj.width, name, diagnostics)
                args.append(f"signal {formal} : out {typ}")

            out.append(f"    procedure {sp.name}({'; '.join(args)}) is")
            for v in sp.locals:
                out.append(f"        variable {v.name} : {self._type_from_width(v.width,v.name,diagnostics)};")
            out.append("    begin")

            body = sp.body
            for original, formal in sorted(outer_map.items(), key=lambda kv: len(kv[0]), reverse=True):
                body = re.sub(rf"\b{re.escape(original)}\b", formal, body)

            hp=_HelperProceduralTranslator(self,body,diagnostics,widths,sp.line or 1,sp,True)
            for ln in hp.parse_block(set()): out.append("        "+ln)
            out.append(f"    end procedure {sp.name};")
        self._signed_names = saved_signed
        return out

    @staticmethod
    def _translate_memory_reads(expr: str, module: Module) -> str:
        for mem in module.memories:
            expr = re.sub(rf"\b{re.escape(mem.name)}\s*\[\s*([^\]]+)\s*\]", rf"{mem.name}(to_integer(unsigned(\1)))", expr)
        return expr

    def _load_mem_values(self, module: Module, mem: Memory, diagnostics: List[Diagnostic]) -> Optional[list[str]]:
        if not module.source_path or not mem.init_file:
            diagnostics.append(Diagnostic("error", f"Cannot resolve memory initialization file for {mem.name}.", mem.line)); return None
        path = module.source_path.parent / mem.init_file
        if not path.exists():
            diagnostics.append(Diagnostic("error", f"Memory initialization file not found: {path}", mem.line)); return None
        raw=[]
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = re.sub(r"//.*", "", line).strip()
            if not line: continue
            raw.extend(line.split())
        count = abs(mem.hi-mem.lo)+1
        width = self._constant_width(mem.elem_width) or 8
        vals=[]
        for i in range(count):
            token = raw[i] if i < len(raw) else "0"
            try: value=int(token.replace("_",""),16)
            except ValueError:
                diagnostics.append(Diagnostic("error", f"Invalid hex value in {mem.init_file}: {token}", mem.line)); return None
            if width == 1: vals.append(f"'{value & 1}'")
            else: vals.append(f"std_logic_vector(to_unsigned({value}, {width}))")
        if len(raw) > count:
            diagnostics.append(Diagnostic("warning", f"{mem.init_file} contains more than {count} values; extras ignored.", mem.line))
        return vals

    def _build_width_map(self, module: Module, reg_ports: List[Port]) -> dict[str, Optional[int]]:
        widths: dict[str, Optional[int]] = {}
        for p in module.ports:
            widths[p.name] = self._constant_width(p.width)
        for s in module.signals:
            if s.kind != "integer":
                widths[s.name] = self._constant_width(s.width)
        for param in module.parameters:
            if param.width is not None:
                widths[param.name] = self._constant_width(param.width)
        for lp in module.localparams:
            # Sized localparams participate in expression typing exactly like
            # packed Verilog constants. Untyped localparams remain INTEGER and
            # are therefore intentionally absent from the packed-width map.
            if lp.width is not None:
                widths[lp.name] = self._constant_width(lp.width)
        for p in reg_ports:
            widths[p.name + "_reg"] = self._constant_width(p.width)
        return widths

    @staticmethod
    def _constant_int_expr(expr: str) -> Optional[int]:
        """Safely evaluate a literal-only integer expression used in a range."""
        try:
            node = ast.parse(expr.strip(), mode="eval")
        except (SyntaxError, ValueError):
            return None

        def walk(n):
            if isinstance(n, ast.Expression):
                return walk(n.body)
            if isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
                return int(n.value)
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
                value = walk(n.operand)
                if value is None:
                    return None
                return value if isinstance(n.op, ast.UAdd) else -value
            if isinstance(n, ast.BinOp) and isinstance(
                n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
            ):
                left, right = walk(n.left), walk(n.right)
                if left is None or right is None:
                    return None
                if isinstance(n.op, ast.Add):
                    return left + right
                if isinstance(n.op, ast.Sub):
                    return left - right
                if isinstance(n.op, ast.Mult):
                    return left * right
                if isinstance(n.op, (ast.Div, ast.FloorDiv)):
                    if right == 0:
                        return None
                    return left // right
                if isinstance(n.op, ast.Mod):
                    if right == 0:
                        return None
                    return left % right
            return None

        return walk(node)

    @staticmethod
    def _constant_width(width: Optional[str]) -> Optional[int]:
        if width is None:
            return 1
        m = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", width, re.S)
        if not m:
            return None
        hi = VHDLGenerator._constant_int_expr(m.group(1))
        lo = VHDLGenerator._constant_int_expr(m.group(2))
        if hi is None or lo is None:
            return None
        return abs(hi - lo) + 1

    @staticmethod
    def _has_vector_width(width: Optional[str]) -> bool:
        return bool(width and re.fullmatch(r"\[.*:.*\]", width.strip(), re.S))

    @staticmethod
    def _bit_literal_value(expr: str) -> Optional[int]:
        m = re.fullmatch(r"\s*1\s*'\s*[bBdDhHoO]\s*([01])\s*", expr)
        if m:
            return int(m.group(1))
        return None

    def _blocking_only_temporaries(self, body: str) -> dict[str, str]:
        """Find architecture regs used purely with blocking assignment in this sequential block.

        Such regs are commonly used as same-cycle scratch values in synthesizable Verilog.
        VHDL signal assignments would change their semantics, so promote them to process
        variables and rename them locally.
        """
        assigned_blocking = set()
        assigned_nonblocking = set()

        # Ignore comparisons by requiring a simple procedural assignment operator.
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(<=|=(?!=))", body):
            name, op = m.groups()
            if op == "<=":
                assigned_nonblocking.add(name)
            else:
                assigned_blocking.add(name)

        signal_map = getattr(self, "_module_signal_map", {})
        candidates = (assigned_blocking - assigned_nonblocking) & set(signal_map)
        candidates = {name for name in candidates if signal_map[name].kind != "integer"}
        return {name: f"{name}_var" for name in sorted(candidates)}

    def _replace_proc_names(self, body: str, mapping: dict[str, str]) -> str:
        if not mapping:
            return body
        # Longest names first avoids any accidental partial replacement.
        pattern = r"\b(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")\b"
        return re.sub(pattern, lambda m: mapping[m.group(1)], body)

    def _process_variable_declarations(self, mapping: dict[str, str], diagnostics: List[Diagnostic]) -> list[str]:
        out = []
        signal_map = getattr(self, "_module_signal_map", {})
        for original, local in mapping.items():
            sig = signal_map[original]
            out.append(f"        variable {local} : {self._type_from_width(sig.width, original, diagnostics)};")
        return out

    def _generate_always(self, block: AlwaysBlock, lines: List[str], diagnostics: List[Diagnostic], reg_ports: List[Port], wire_proxy_ports: List[Port], signal_widths: dict[str, Optional[int]], sensitivity_signals: set[str], subprograms: List[Subprogram]) -> None:
        m = re.fullmatch(r"posedge\s+([A-Za-z_]\w*)", block.sensitivity)
        if m:
            clk = m.group(1)
            var_map = self._blocking_only_temporaries(block.body)
            proc_body = self._replace_proc_names(block.body, var_map)
            local_widths = dict(signal_widths)
            for original, local in var_map.items():
                local_widths[local] = signal_widths.get(original)
                if original in getattr(self, "_signed_names", set()):
                    self._signed_names.add(local)

            lines.append(f"    process({clk})")
            lines.extend(self._process_variable_declarations(var_map, diagnostics))
            lines.extend(["    begin", f"        if rising_edge({clk}) then"])
            body_lines = self._translate_proc_body(proc_body, diagnostics, reg_ports, wire_proxy_ports, local_widths, block.line, sequential=True, process_variables=set(var_map.values()))
            lines.extend(["            " + ln if ln else "" for ln in body_lines])
            lines.extend(["        end if;", "    end process;"])
            return

        # Common synthesizable asynchronous-reset sequential form:
        #   always @(posedge clk or posedge reset)
        #   always @(posedge clk, posedge reset)
        # and the active-low equivalent with negedge reset.
        #
        # The Verilog body must make the asynchronous control the outermost if,
        # which is the normal FPGA coding template.  We translate that body first,
        # then lift its reset branch outside rising_edge() for equivalent VHDL.
        am = re.fullmatch(
            r"posedge\s+([A-Za-z_]\w*)\s*(?:or|,)\s*(posedge|negedge)\s+([A-Za-z_]\w*)",
            block.sensitivity,
            re.I,
        )
        if am:
            clk, reset_edge, reset = am.groups()
            reset_edge = reset_edge.lower()
            var_map = self._blocking_only_temporaries(block.body)
            proc_body = self._replace_proc_names(block.body, var_map)
            local_widths = dict(signal_widths)
            for original, local in var_map.items():
                local_widths[local] = signal_widths.get(original)
                if original in getattr(self, "_signed_names", set()):
                    self._signed_names.add(local)

            body_lines = self._translate_proc_body(
                proc_body, diagnostics, reg_ports, wire_proxy_ports,
                local_widths, block.line, sequential=True,
                process_variables=set(var_map.values())
            )
            split = self._split_async_reset_body(body_lines, reset, reset_edge)
            if split is None:
                diagnostics.append(Diagnostic(
                    "error",
                    f"Asynchronous reset block must use {reset} as the outermost reset condition.",
                    block.line,
                ))
                lines.append(
                    f"    -- ERROR: unsupported asynchronous-reset body from Verilog line {block.line}"
                )
                return

            reset_lines, clock_lines = split
            active = "'1'" if reset_edge == "posedge" else "'0'"
            lines.append(f"    process({clk}, {reset})")
            lines.extend(self._process_variable_declarations(var_map, diagnostics))
            lines.extend([
                "    begin",
                f"        if {reset} = {active} then",
            ])
            lines.extend(["            " + ln if ln else "" for ln in reset_lines])
            lines.append(f"        elsif rising_edge({clk}) then")
            lines.extend(["            " + ln if ln else "" for ln in clock_lines])
            lines.extend(["        end if;", "    end process;"])
            return

        if block.sensitivity == "*":
            sensitivity = self._infer_comb_sensitivity(block.body, signal_widths, sensitivity_signals, subprograms)
            reg_proxy = {p.name: p.name + "_reg" for p in reg_ports}
            wire_proxy = {p.name: p.name + "_int" for p in wire_proxy_ports}
            sensitivity = [reg_proxy.get(n, wire_proxy.get(n, n)) for n in sensitivity]
            if not sensitivity:
                diagnostics.append(Diagnostic("error", "Could not infer a sensitivity list for always @(*).", block.line))
                lines.append(f"    -- ERROR: could not infer combinational sensitivity list from Verilog line {block.line}")
                return
            integer_vars = [
                name for name in sorted(getattr(self, "_integer_signal_names", set()))
                if re.search(rf"\b{re.escape(name)}\b", block.body)
            ]
            lines.append(f"    process({', '.join(sensitivity)})")
            for name in integer_vars:
                lines.append(f"        variable {name} : integer;")
            lines.append("    begin")
            body_lines = self._translate_proc_body(
                block.body, diagnostics, reg_ports, wire_proxy_ports,
                signal_widths, block.line, sequential=False,
                process_variables=set(integer_vars)
            )
            lines.extend(["        " + ln if ln else "" for ln in body_lines])
            lines.append("    end process;")
            return

        diagnostics.append(Diagnostic("error", f"Unsupported always sensitivity list: @({block.sensitivity})", block.line))
        lines.append(f"    -- ERROR: unsupported always @({block.sensitivity}) from Verilog line {block.line}")

    @staticmethod
    def _split_async_reset_body(body_lines: List[str], reset: str, reset_edge: str) -> Optional[tuple[List[str], List[str]]]:
        """Split translated `if reset ... else ...` into async reset and clock branches."""
        if not body_lines:
            return None

        first = body_lines[0].strip()
        m = re.fullmatch(r"if\s+(.+)\s+then", first)
        if not m:
            return None

        cond = m.group(1).strip()
        active = "1" if reset_edge == "posedge" else "0"
        # Accept the forms produced by _condition for scalar reset tests.
        if reset not in cond:
            return None
        if reset_edge == "posedge":
            if not (re.search(rf"\b{re.escape(reset)}\b\s*=\s*'1'", cond) or cond == reset):
                return None
        else:
            if not (
                re.search(rf"\b{re.escape(reset)}\b\s*=\s*'0'", cond)
                or re.search(rf"\bnot\s+{re.escape(reset)}\b", cond, re.I)
            ):
                return None

        depth = 1
        boundary = None
        boundary_kind = None
        final_end = None

        for i in range(1, len(body_lines)):
            s = body_lines[i].strip()
            if s.startswith("if ") and s.endswith(" then"):
                depth += 1
            elif s == "end if;":
                depth -= 1
                if depth == 0:
                    final_end = i
                    break
            elif depth == 1 and boundary is None and s == "else":
                boundary = i
                boundary_kind = "else"
            elif depth == 1 and boundary is None and s.startswith("elsif ") and s.endswith(" then"):
                boundary = i
                boundary_kind = "elsif"

        if boundary is None or final_end is None:
            return None

        def dedent(lines_in: List[str]) -> List[str]:
            out = []
            for ln in lines_in:
                out.append(ln[4:] if ln.startswith("    ") else ln)
            return out

        reset_lines = dedent(body_lines[1:boundary])

        if boundary_kind == "else":
            clock_lines = dedent(body_lines[boundary + 1:final_end])
        else:
            # `else if (...)` becomes VHDL `elsif ...`; once moved under
            # rising_edge(), it must become a fresh `if ... end if`.
            first_clock = body_lines[boundary].strip()
            clock_lines = ["if " + first_clock[len("elsif "):]]
            clock_lines.extend(dedent(body_lines[boundary + 1:final_end]))
            clock_lines.append("end if;")

        return reset_lines, clock_lines

    @staticmethod
    def _infer_comb_sensitivity(body: str, signal_widths: dict[str, Optional[int]], sensitivity_signals: set[str], subprograms: List[Subprogram]) -> list[str]:
        """Infer a complete Verilog always @(*) sensitivity list.

        Verilog @(*) includes signals read indirectly by functions called from the
        block.  VHDL-93/2002 explicit process lists do not do that automatically,
        so we walk synthesizable helper-function bodies transitively and include
        any outer signals they read.  This keeps generated code compatible with
        pre-2008 VHDL while matching Verilog's combinational semantics.
        """
        sub_by_name = {sp.name: sp for sp in subprograms}

        def direct_reads(text: str, excluded: set[str] | None = None) -> list[str]:
            excluded = excluded or set()
            scrub = re.sub(r"\b\d+'[bBdDhHoO][0-9a-fA-F_xXzZ]+", " ", text)
            found: list[str] = []
            for name in signal_widths:
                if name not in sensitivity_signals or name in excluded:
                    continue
                # Ignore plain procedural blocking assignments to the name, but
                # do NOT erase comparison operators such as == or <=.  The old
                # pattern treated `pix_y == ...` and `turtle_state == ...` as if
                # they were assignments, causing incomplete @(*) sensitivity.
                #
                # For combinational blocks it is safer to over-include a signal
                # than to omit a real read.  Nonblocking <= on a combinational
                # LHS may therefore remain in the inferred list, which is benign.
                tmp = re.sub(
                    rf"\b{re.escape(name)}\b(?:\s*\[[^\]]+\])?\s*=(?!=)",
                    " ",
                    scrub,
                )
                if re.search(rf"\b{re.escape(name)}\b", tmp):
                    found.append(name)
            return found

        ordered: list[str] = []
        seen_signals: set[str] = set()
        def add(names: list[str]) -> None:
            for name in names:
                if name not in seen_signals:
                    seen_signals.add(name)
                    ordered.append(name)

        add(direct_reads(body))

        visited_subs: set[str] = set()
        def add_subprogram_dependencies(name: str) -> None:
            if name in visited_subs or name not in sub_by_name:
                return
            visited_subs.add(name)
            sp = sub_by_name[name]
            excluded = {sp.name}
            excluded.update(p.name for p in sp.ports)
            excluded.update(v.name for v in sp.locals)
            excluded.update(sp.integer_locals)
            add(direct_reads(sp.body, excluded))
            for child_name in sub_by_name:
                if re.search(rf"\b{re.escape(child_name)}\s*\(", sp.body):
                    add_subprogram_dependencies(child_name)

        for sp_name in sub_by_name:
            if re.search(rf"\b{re.escape(sp_name)}\s*\(", body):
                add_subprogram_dependencies(sp_name)

        return ordered

    def _translate_proc_body(self, body: str, diagnostics: List[Diagnostic], reg_ports: List[Port], wire_proxy_ports: List[Port], signal_widths: dict[str, Optional[int]], base_line: int, sequential: bool, process_variables: Optional[set[str]] = None) -> List[str]:
        try:
            parser = _ProceduralTranslator(self, body, diagnostics, reg_ports, wire_proxy_ports, signal_widths, base_line, sequential, process_variables or set())
            out = parser.parse_block(stop_tokens=set())
            parser.skip_ws()
            if parser.pos < len(parser.text):
                diagnostics.append(Diagnostic("error", f"Unsupported procedural text near: {parser.text[parser.pos:parser.pos+50].strip()}", base_line))
            return out
        except ValueError as exc:
            diagnostics.append(Diagnostic("error", str(exc), base_line))
            return ["-- ERROR: unsupported Verilog procedural statements"]

    def _translate_single_assignment(self, stmt: str, diagnostics: List[Diagnostic], reg_ports: List[Port], wire_proxy_ports: List[Port], signal_widths: dict[str, Optional[int]], line: int, sequential: bool, process_variables: Optional[set[str]] = None) -> str:
        m = re.match(r"(.+?)\s*(<=|=)\s*(.+?)\s*;?$", stmt.strip(), re.S)
        if not m:
            diagnostics.append(Diagnostic("error", f"Unsupported statement: {stmt.strip()}", line))
            return f"-- ERROR: {stmt.strip()}"
        lhs, op, rhs = m.groups()
        process_variables = process_variables or set()
        lhs_vhdl = self._map_lhs(self._translate_selects(lhs.strip(), signal_widths), reg_ports, wire_proxy_ports)
        lhs_base = re.match(r"([A-Za-z_]\w*)", lhs_vhdl)
        lhs_name = lhs_base.group(1) if lhs_base else ""
        use_variable = op == '=' and lhs_name in process_variables
        if sequential and op == '=' and not use_variable:
            diagnostics.append(Diagnostic("warning", "Blocking assignment inside clocked always block translated as a signal assignment; review sequential semantics.", line))
        rhs_mapped = self._map_rhs(rhs.strip(), reg_ports, wire_proxy_ports)

        if use_variable and lhs_name in getattr(self, "_integer_names", set()):
            rhs_vhdl = self._integer_value_expr(rhs_mapped, diagnostics, signal_widths)
            return f"{lhs_vhdl} := {rhs_vhdl};"

        target_width = self._lhs_width(lhs_vhdl, signal_widths)
        base_target = re.match(r"([A-Za-z_]\w*)", lhs_vhdl)
        target_name = base_target.group(1) if base_target else None
        # Keep generated procedural code compatible with baseline VHDL rather
        # than relying on VHDL-2008 conditional expressions/assignments.
        ternary = self._split_ternary(rhs_mapped)
        if ternary:
            cond, yes, no = ternary
            c = self._condition(cond, diagnostics, signal_widths)
            y = self._assignment_rhs(yes, target_width, diagnostics, signal_widths, target_name)
            n = self._assignment_rhs(no, target_width, diagnostics, signal_widths, target_name)
            assign_op = ":=" if use_variable else "<="
            return f"if {c} then\n    {lhs_vhdl} {assign_op} {y};\nelse\n    {lhs_vhdl} {assign_op} {n};\nend if;"
        if target_width == 1 and self._looks_boolean(rhs_mapped):
            c = self._condition(rhs_mapped, diagnostics, signal_widths)
            assign_op = ":=" if use_variable else "<="
            return f"if {c} then\n    {lhs_vhdl} {assign_op} '1';\nelse\n    {lhs_vhdl} {assign_op} '0';\nend if;"
        assign_op = ":=" if use_variable else "<="
        return f"{lhs_vhdl} {assign_op} {self._assignment_rhs(rhs_mapped, target_width, diagnostics, signal_widths, target_name)};"

    def _lhs_width(self, lhs: str, signal_widths: dict[str, Optional[int]]) -> Optional[int]:
        lhs = lhs.strip()
        m = re.fullmatch(r"([A-Za-z_]\w*)\((.+?)\s+downto\s+(.+?)\)", lhs)
        if m and m.group(2).isdigit() and m.group(3).isdigit():
            return abs(int(m.group(2))-int(m.group(3)))+1

        # A translated unpacked-memory element uses VHDL array indexing syntax,
        # e.g. calibration(i).  That is an entire memory element, not a packed
        # one-bit select.  Check memory element widths before the generic
        # parenthesized-select rule.
        m = re.fullmatch(r"([A-Za-z_]\w*)\((.+)\)", lhs, re.S)
        if m and m.group(1) in getattr(self, "_memory_widths", {}):
            return self._memory_widths.get(m.group(1))

        # Any packed bit select is one bit wide, including translated dynamic
        # indexes that contain nested calls such as to_integer(unsigned(idx)).
        if re.match(r"^[A-Za-z_]\w*\(", lhs) and " downto " not in lhs:
            return 1
        m = re.match(r"([A-Za-z_]\w*)", lhs)
        return signal_widths.get(m.group(1)) if m else None

    @staticmethod
    def _looks_boolean(expr: str) -> bool:
        return bool(re.search(r"&&|\|\||==|!=|<=|>=|(?<![<])<(?![<])|(?<![>])>(?![>])|^\s*!", expr))

    def _target_width_numeric_expr(self, expr: str, target_width: int, diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]]) -> Optional[str]:
        """Render supported mixed integer/vector arithmetic directly at a destination width.

        This models Verilog's context sizing for expressions used as module input
        actuals and assignment RHS values. It avoids accidentally sizing an unsized
        literal to a narrow operand (for example 220 + five_bit_index*8).
        """
        expr = self._strip_outer_parens(expr.strip())
        split = self._split_top_level_binary(expr, ['+','-'])
        if split:
            a, op, b = split
            if self._is_integer_expr(a) and self._is_integer_expr(b):
                return None
            def term(raw: str) -> str:
                raw = raw.strip()
                bit_value = self._bit_literal_value(raw)
                if bit_value is not None:
                    return f"to_unsigned({bit_value}, {target_width})"
                if self._is_integer_expr(raw):
                    return f"to_unsigned({self._integer_expr(raw, diagnostics, signal_widths)}, {target_width})"
                nested = self._target_width_numeric_expr(raw, target_width, diagnostics, signal_widths)
                if nested is not None:
                    return f"unsigned({nested})"
                rendered = self._expr(raw, diagnostics, signal_widths)
                if self._raw_expr_width(raw, signal_widths) == 1:
                    return self._as_unsigned(raw, rendered, target_width, signal_widths)
                return f"resize(unsigned({rendered}), {target_width})"
            return f"std_logic_vector({term(a)} {op} {term(b)})"

        split = self._split_top_level_binary(expr, ['*'])
        if split:
            a, _op, b = split
            if self._is_integer_expr(a) and self._is_integer_expr(b):
                return None

            # Prefer numeric_std's UNSIGNED * NATURAL overload whenever one side
            # is a numeric literal. Vivado resolves this much more reliably than
            # UNSIGNED * UNSIGNED when one operand came from a Verilog concat.
            a_sized = self._sized_literal_integer(a.strip())
            b_sized = self._sized_literal_integer(b.strip())

            if b_sized is not None or self._is_integer_expr(b):
                va = self._expr(a, diagnostics, signal_widths)
                n_text = b_sized if b_sized is not None else self._integer_expr(b, diagnostics, signal_widths)

                # When the vector width and literal are both known and the
                # worst-case product fits in 31-bit positive VHDL INTEGER range,
                # lower through integer arithmetic.  This avoids Vivado's
                # numeric_std '*' overload ambiguity for concat-derived vectors.
                wa = self._raw_expr_width(a, signal_widths)
                if b_sized is not None and wa is not None and wa > 0:
                    b_value = int(b_sized)
                    max_product = ((1 << wa) - 1) * b_value
                    if max_product <= 2_147_483_647:
                        return (
                            f"std_logic_vector(to_unsigned("
                            f"to_integer(unsigned({va})) * {b_value}, "
                            f"{target_width}))"
                        )

                return f"std_logic_vector(resize(unsigned({va}) * {n_text}, {target_width}))"

            if a_sized is not None or self._is_integer_expr(a):
                vb = self._expr(b, diagnostics, signal_widths)
                n_text = a_sized if a_sized is not None else self._integer_expr(a, diagnostics, signal_widths)

                wb = self._raw_expr_width(b, signal_widths)
                if a_sized is not None and wb is not None and wb > 0:
                    a_value = int(a_sized)
                    max_product = ((1 << wb) - 1) * a_value
                    if max_product <= 2_147_483_647:
                        return (
                            f"std_logic_vector(to_unsigned("
                            f"{a_value} * to_integer(unsigned({vb})), "
                            f"{target_width}))"
                        )

                return f"std_logic_vector(resize(unsigned({vb}) * {n_text}, {target_width}))"

            va = self._expr(a, diagnostics, signal_widths)
            vb = self._expr(b, diagnostics, signal_widths)
            return f"std_logic_vector(resize(unsigned({va}) * unsigned({vb}), {target_width}))"
        return None

    def _integer_value_expr(self, expr: str, diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]]) -> str:
        """Render a supported Verilog expression as VHDL INTEGER arithmetic."""
        expr = self._strip_outer_parens(expr.strip())

        sized = self._sized_literal_integer(expr)
        if sized is not None:
            return sized
        if re.fullmatch(r"\d[\d_]*", expr):
            return expr.replace("_", "")
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            if expr in getattr(self, "_integer_names", set()):
                return expr
            if expr in signal_widths:
                width = signal_widths.get(expr)
                if width == 1:
                    return f"to_integer(unsigned'(0 => {expr}))"
                return f"to_integer(unsigned({expr}))"
            return expr

        for ops in [['*','/','%'], ['+','-']]:
            split = self._split_top_level_binary(expr, ops)
            if split:
                a, op, b = split
                vop = "mod" if op == "%" else op
                return (
                    f"({self._integer_value_expr(a, diagnostics, signal_widths)} "
                    f"{vop} "
                    f"{self._integer_value_expr(b, diagnostics, signal_widths)})"
                )

        diagnostics.append(Diagnostic("error", f"Could not translate integer expression: {expr}"))
        return expr

    def _assignment_rhs(self, rhs: str, target_width: Optional[int], diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]], target_name: Optional[str] = None) -> str:
        rhs = rhs.strip()
        if target_name and target_name in getattr(self, "_integer_signal_names", set()):
            return self._integer_expr(rhs, diagnostics, signal_widths)

        # Parameterized replication of a one-bit constant is a common reset idiom:
        #   {COUNT_WIDTH{1'b0}}
        # In assignment context VHDL's aggregate preserves the destination width.
        rm = re.fullmatch(
            r"\{\s*(?:[A-Za-z_]\w*|\d+)\s*\{\s*1'[bB]([01xXzZ])\s*\}\s*\}",
            rhs,
            re.S,
        )
        if rm:
            return f"(others => '{rm.group(1).upper()}')"

        # Unknown/generic vector widths can still be assigned zero without knowing
        # their numeric length. VHDL's aggregate preserves the destination width.
        if target_width is None and target_name and signal_widths.get(target_name) is None and re.fullmatch(r"\s*(?:\d+'[dDbBhHoO])?0\s*", rhs):
            return "(others => '0')"
        if target_width == 1 and self._looks_boolean(rhs):
            return f"'1' when {self._condition(rhs, diagnostics, signal_widths)} else '0'"
        if target_width == 1 and re.fullmatch(r"(?:1\s*'\s*[bBdDhH]\s*)?[01]", rhs):
            bit = rhs[-1]
            return f"'{bit}'"
        if target_width and target_width > 1 and self._is_integer_expr(rhs):
            iv = self._integer_expr(rhs, diagnostics, signal_widths)
            if target_name and target_name in getattr(self, "_signed_names", set()):
                return f"std_logic_vector(to_signed({iv}, {target_width}))"
            return f"std_logic_vector(to_unsigned({iv}, {target_width}))"
        # A conditional expression can have integer-valued branches (commonly
        # localparam color constants) while the assignment target is a vector.
        # Verilog coerces each branch to the target width; VHDL needs that made
        # explicit on both sides of the conditional expression.
        if target_width and target_width > 1:
            ternary = self._split_ternary(rhs)
            if ternary:
                cond, yes, no = ternary
                if self._is_integer_expr(yes) and self._is_integer_expr(no):
                    y = self._integer_expr(yes, diagnostics, signal_widths)
                    n = self._integer_expr(no, diagnostics, signal_widths)
                    c = self._condition(cond, diagnostics, signal_widths)
                    return f"std_logic_vector(to_unsigned({y}, {target_width})) when {c} else std_logic_vector(to_unsigned({n}, {target_width}))"
        if target_width and target_width > 1 and not self._is_signed_expr(rhs):
            contextual = self._target_width_numeric_expr(rhs, target_width, diagnostics, signal_widths)
            if contextual is not None:
                return contextual
        rendered = self._expr(rhs, diagnostics, signal_widths)
        source_width = self._raw_expr_width(rhs, signal_widths)
        # Verilog automatically zero-extends or truncates unsigned values on assignment.
        # VHDL requires the resize to be explicit.
        if target_width and target_width > 1 and source_width and source_width != target_width:
            if self._is_signed_expr(rhs):
                return f"std_logic_vector(resize(signed({rendered}), {target_width}))"
            return f"std_logic_vector(resize(unsigned({rendered}), {target_width}))"
        return rendered

    def _concurrent_assignment_rhs(
        self,
        rhs: str,
        target_width: Optional[int],
        diagnostics: List[Diagnostic],
        signal_widths: dict[str, Optional[int]],
        target_name: Optional[str] = None,
    ) -> str:
        """Render a continuous-assignment RHS for baseline VHDL.

        A right-associated Verilog ternary chain maps naturally to a VHDL
        conditional signal assignment.  Flatten the false-branch chain instead
        of recursively creating VHDL conditional *expressions*, which require
        VHDL-2008 in contexts Vivado may otherwise read as baseline VHDL.
        """
        rhs = rhs.strip()
        parts: List[tuple[str, str]] = []
        current = rhs

        while True:
            ternary = self._split_ternary(current)
            if not ternary:
                break
            cond, yes, no = ternary

            # The common synthesizable mux-chain form nests on the false side:
            # c0 ? a : c1 ? b : c.  If the true side itself contains a
            # ternary, fall back to the normal expression path so it can be
            # diagnosed/handled explicitly rather than silently reordered.
            if self._split_ternary(yes):
                return self._assignment_rhs(
                    rhs, target_width, diagnostics, signal_widths, target_name
                )

            yes_vhdl = self._assignment_rhs(
                yes, target_width, diagnostics, signal_widths, target_name
            )
            cond_vhdl = self._condition(cond, diagnostics, signal_widths)
            parts.append((yes_vhdl, cond_vhdl))
            current = no

        if not parts:
            return self._assignment_rhs(
                rhs, target_width, diagnostics, signal_widths, target_name
            )

        final_vhdl = self._assignment_rhs(
            current, target_width, diagnostics, signal_widths, target_name
        )

        rendered = ""
        for value_vhdl, cond_vhdl in parts:
            rendered += f"{value_vhdl} when {cond_vhdl} else "
        rendered += final_vhdl
        return rendered

    def _map_lhs(self, lhs: str, reg_ports: List[Port], wire_proxy_ports: List[Port]) -> str:
        for p in reg_ports:
            if lhs == p.name:
                return p.name + "_reg"
            if lhs.startswith(p.name + "("):
                return p.name + "_reg" + lhs[len(p.name):]
        for p in wire_proxy_ports:
            if lhs == p.name:
                return p.name + "_int"
            if lhs.startswith(p.name + "("):
                return p.name + "_int" + lhs[len(p.name):]
        return lhs

    @staticmethod
    def _map_rhs(expr: str, reg_ports: List[Port], wire_proxy_ports: List[Port]) -> str:
        for p in reg_ports:
            expr = re.sub(rf"\b{re.escape(p.name)}\b", f"{p.name}_reg", expr)
        for p in wire_proxy_ports:
            expr = re.sub(rf"\b{re.escape(p.name)}\b", f"{p.name}_int", expr)
        return expr

    def _type_from_width(self, width: Optional[str], name: str, diagnostics: List[Diagnostic]) -> str:
        if not width:
            return "std_logic"
        m = re.match(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", width)
        if not m:
            diagnostics.append(Diagnostic("error", f"Could not parse width {width} for {name}."))
            return "std_logic_vector(0 downto 0)"
        hi, lo = m.groups()
        return f"std_logic_vector({self._simple_index_expr(hi)} downto {self._simple_index_expr(lo)})"

    @staticmethod
    def _whole_call_parts(expr: str) -> Optional[tuple[str, str]]:
        expr = expr.strip()
        m = re.match(r"([A-Za-z_]\w*)\s*\(", expr)
        if not m:
            return None
        name = m.group(1)
        open_pos = expr.find("(", m.start(1) + len(name))
        depth = 0
        in_string = False
        escape = False
        close_pos = None
        for i in range(open_pos, len(expr)):
            ch = expr[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break
        if close_pos is None or expr[close_pos + 1:].strip():
            return None
        return name, expr[open_pos + 1:close_pos]

    def _condition(self, cond: str, diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]]) -> str:
        cond = self._strip_outer_parens(cond.strip())

        # Top-level logical OR / AND.
        split = self._split_top_level_binary(cond, ['||'])
        if split:
            a, op, b = split
            return f"({self._condition(a, diagnostics, signal_widths)}) or ({self._condition(b, diagnostics, signal_widths)})"
        split = self._split_top_level_binary(cond, ['&&'])
        if split:
            a, op, b = split
            return f"({self._condition(a, diagnostics, signal_widths)}) and ({self._condition(b, diagnostics, signal_widths)})"

        if cond.startswith('!') and not cond.startswith('!='):
            return f"not ({self._condition(cond[1:].strip(), diagnostics, signal_widths)})"
        if cond.startswith('~'):
            # In one-bit/boolean condition context Verilog bitwise NOT is logical NOT.
            return f"not ({self._condition(cond[1:].strip(), diagnostics, signal_widths)})"

        # Reduction operators produce std_logic.  Verilog permits that one-bit
        # value directly in a condition; VHDL BOOLEAN context requires an
        # explicit comparison.
        red = re.fullmatch(r"([&|^])\s*(.+)", cond, re.S)
        if red:
            return f"{self._expr(cond, diagnostics, signal_widths)} = '1'"

        m = re.fullmatch(r"([A-Za-z_]\w*)", cond)
        if m and signal_widths.get(m.group(1), 1) == 1:
            return f"{m.group(1)} = '1'"

        call = self._whole_call_parts(cond)
        if call:
            fname, _args = call
            sp = getattr(self, "_subprogram_map", {}).get(fname)
            if sp is not None and sp.kind == "function" and self._constant_width(sp.return_width) == 1:
                return f"{self._expr(cond, diagnostics, signal_widths)} = '1'"
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]", cond)
        if m:
            return f"{self._translate_selects(cond, signal_widths)} = '1'"

        # Comparisons produce VHDL booleans. _expr converts literals/selects and
        # arithmetic while preserving comparison operators.
        return self._expr(cond, diagnostics, signal_widths)

    @staticmethod
    def _sized_literal_integer(expr: str) -> Optional[str]:
        """Return the integer value of a simple Verilog sized literal.

        Untyped parameter/localparam declarations are represented as VHDL INTEGERs
        in this translator.  A Verilog RHS such as 12'hFFF therefore needs the
        integer value 4095, not a std_logic_vector constructor.
        """
        m = re.fullmatch(r"\s*(\d+)'(?:[sS])?([bBdDhHoO])([0-9a-fA-F_]+)\s*", expr)
        if not m:
            return None
        _width, base_ch, digits = m.groups()
        base = {'b':2,'d':10,'h':16,'o':8}[base_ch.lower()]
        try:
            return str(int(digits.replace('_',''), base))
        except ValueError:
            return None

    def _initializer_expr(self, expr: str, width: Optional[str], diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]]) -> str:
        expr = expr.strip()
        width_n = self._constant_width(width)
        if width_n and self._is_integer_expr(expr):
            value_expr = self._integer_expr(expr, diagnostics, signal_widths)
            if width_n == 1:
                # Initializers for one-bit RTL in our supported subset are normally
                # constants. Preserve a clear diagnostic for anything ambiguous.
                if re.fullmatch(r"\d+", expr):
                    return f"'{int(expr) & 1}'"
            if width_n > 1:
                return f"std_logic_vector(to_unsigned({value_expr}, {width_n}))"
        return self._expr(expr, diagnostics, signal_widths)

    def _is_integer_expr(self, expr: str) -> bool:
        expr = self._strip_outer_parens(expr.strip())
        if expr.startswith('-') and not expr.startswith('--'):
            return self._is_integer_expr(expr[1:].strip())
        cm = re.fullmatch(r"\$clog2\s*\((.*)\)", expr, re.S)
        if cm:
            return self._is_integer_expr(cm.group(1).strip())
        if re.fullmatch(r"[A-Za-z_]\w*", expr) and expr in getattr(self, "_integer_local_names", set()):
            return True
        if re.fullmatch(r"\d[\d_]*", expr):
            return True
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            return expr in getattr(self, "_integer_names", set())
        split = self._split_top_level_binary(expr, ['+','-','*','/'])
        if split:
            a, _op, b = split
            return self._is_integer_expr(a) and self._is_integer_expr(b)
        return False

    def _integer_expr(self, expr: str, diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]]) -> str:
        expr = self._strip_outer_parens(expr.strip())
        if expr.startswith('-') and not expr.startswith('--'):
            return f"-({self._integer_expr(expr[1:].strip(), diagnostics, signal_widths)})"
        cm = re.fullmatch(r"\$clog2\s*\((.*)\)", expr, re.S)
        if cm:
            return f"clog2({self._integer_expr(cm.group(1).strip(), diagnostics, signal_widths)})"
        if re.fullmatch(r"[A-Za-z_]\w*", expr) and expr in getattr(self, "_integer_local_names", set()):
            return expr
        if re.fullmatch(r"\d[\d_]*", expr):
            return expr.replace("_", "")
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            return self._source_ident(expr)
        split = self._split_top_level_binary(expr, ['+','-'])
        if split:
            a, op, b = split
            return f"({self._integer_expr(a, diagnostics, signal_widths)} {op} {self._integer_expr(b, diagnostics, signal_widths)})"
        split = self._split_top_level_binary(expr, ['*','/'])
        if split:
            a, op, b = split
            return f"({self._integer_expr(a, diagnostics, signal_widths)} {op} {self._integer_expr(b, diagnostics, signal_widths)})"
        diagnostics.append(Diagnostic("error", f"Expected integer expression but found: {expr}"))
        return expr

    def _raw_expr_width(self, expr: str, signal_widths: dict[str, Optional[int]]) -> Optional[int]:
        """Infer Verilog value width for the supported expression subset."""
        expr = self._strip_outer_parens(expr.strip())
        if self._is_integer_expr(expr):
            return None
        sm = re.fullmatch(r"\$signed\s*\(([^()]*)\)", expr, re.S)
        if sm:
            return self._raw_expr_width(sm.group(1).strip(), signal_widths)
        m = re.fullmatch(r"(\d+)'(?:[sS])?[bBdDhHoO]([0-9a-fA-F_xXzZ]+)", expr)
        if m:
            return int(m.group(1))
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*([^\]:]+)\s*:\s*([^\]]+)\s*\]", expr)
        if m and m.group(2).strip().isdigit() and m.group(3).strip().isdigit():
            return abs(int(m.group(2))-int(m.group(3)))+1
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]", expr)
        if m:
            # For unpacked memories, Verilog brackets select an entire memory
            # element, not one packed bit. Preserve the element width.
            if m.group(1) in getattr(self, "_memory_widths", {}):
                return self._memory_widths.get(m.group(1))
            return 1
        # Memory reads may already have been lowered from Verilog brackets to
        # VHDL array indexing before width inference (e.g. data_bytes(1)).
        mm = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.+)\)", expr, re.S)
        if mm and mm.group(1) in getattr(self, "_memory_widths", {}):
            return self._memory_widths.get(mm.group(1))
        if re.fullmatch(r'"."', expr, re.S):
            return 8
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            return signal_widths.get(expr)
        if expr.startswith('{') and expr.endswith('}'):
            parts = VerilogSubsetParser._split_top_level_commas(expr[1:-1])
            widths = [self._raw_expr_width(p, signal_widths) for p in parts]
            if all(w is not None for w in widths):
                return sum(widths)
        for ops in [['>>>','>>','<<'], ['*','/','%'], ['+','-']]:
            split = self._split_top_level_binary(expr, ops)
            if split:
                a, _op, b = split
                wa, wb = self._raw_expr_width(a, signal_widths), self._raw_expr_width(b, signal_widths)
                if ops == ['>>>','>>','<<']:
                    return wa
                if wa is None: return wb
                if wb is None: return wa
                return max(wa, wb)
        return None

    def _is_signed_expr(self, expr: str) -> bool:
        expr = self._strip_outer_parens(expr.strip())
        if re.fullmatch(r"\$signed\s*\(([^()]*)\)", expr, re.S):
            return True
        if re.fullmatch(r"[A-Za-z_]\w*", expr):
            return expr in getattr(self, "_signed_names", set())
        split = self._split_top_level_binary(expr, ['>>>'])
        if split:
            return True
        split = self._split_top_level_binary(expr, ['+','-'])
        if split:
            a, _op, b = split
            return self._is_signed_expr(a) or self._is_signed_expr(b)
        split = self._split_top_level_binary(expr, ['*'])
        if split:
            a, _op, b = split
            return self._is_signed_expr(a) or self._is_signed_expr(b)
        return False

    def _as_signed(self, raw: str, rendered: str, width: int, signal_widths: dict[str, Optional[int]]) -> str:
        bit_value = self._bit_literal_value(raw)
        if bit_value is not None:
            return f"to_signed({bit_value}, {width})"
        rw = self._raw_expr_width(raw, signal_widths)
        if rw is None and self._is_integer_expr(raw):
            return f"to_signed({self._integer_expr(raw, [], signal_widths)}, {width})"
        if rw == width and width > 1:
            return f"signed({rendered})"
        return f"resize(signed({rendered}), {width})"

    def _as_unsigned(self, raw: str, rendered: str, width: int, signal_widths: dict[str, Optional[int]]) -> str:
        bit_value = self._bit_literal_value(raw)
        if bit_value is not None:
            return f"to_unsigned({bit_value}, {width})"
        rw = self._raw_expr_width(raw, signal_widths)
        if rw is None and self._is_integer_expr(raw):
            return f"to_unsigned({self._integer_expr(raw, [], signal_widths)}, {width})"
        if rw == width and width > 1:
            return f"unsigned({rendered})"
        if rw == 1:
            if width <= 1:
                return f"unsigned(std_logic_vector'(0 => {rendered}))"
            zeros = "0" * (width - 1)
            return f"unsigned(std_logic_vector'(\"{zeros}\" & {rendered}))"
        return f"resize(unsigned({rendered}), {width})"

    @staticmethod
    def _strip_outer_parens(expr: str) -> str:
        expr = expr.strip()
        while expr.startswith('(') and expr.endswith(')'):
            depth = 0
            balanced = True
            for i, ch in enumerate(expr):
                if ch == '(': depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0 and i != len(expr)-1:
                        balanced = False; break
            if balanced and depth == 0:
                expr = expr[1:-1].strip()
            else:
                break
        return expr

    @staticmethod
    def _split_ternary(expr: str) -> Optional[tuple[str, str, str]]:
        # Return condition, true expression, false expression for a top-level
        # Verilog ?: expression. Handles nested ternaries by pairing ':' with '?'.
        pdepth = bdepth = cdepth = 0
        qpos = None
        nested_q = 0
        for i, ch in enumerate(expr):
            if ch == '(': pdepth += 1
            elif ch == ')': pdepth = max(0, pdepth-1)
            elif ch == '[': bdepth += 1
            elif ch == ']': bdepth = max(0, bdepth-1)
            elif ch == '{': cdepth += 1
            elif ch == '}': cdepth = max(0, cdepth-1)
            elif pdepth == 0 and bdepth == 0 and cdepth == 0:
                if ch == '?':
                    if qpos is None:
                        qpos = i
                    else:
                        nested_q += 1
                elif ch == ':' and qpos is not None:
                    if nested_q:
                        nested_q -= 1
                    else:
                        return expr[:qpos].strip(), expr[qpos+1:i].strip(), expr[i+1:].strip()
        return None

    @staticmethod
    def _split_top_level_binary(expr: str, operators: list[str]) -> Optional[tuple[str, str, str]]:
        pdepth = bdepth = cdepth = 0

        # Operators appearing inside Verilog string literals are data, not
        # syntax.  Character-valued RTL commonly uses "+", "-", "|", etc.
        # Mark quoted positions once so the right-to-left precedence scan cannot
        # accidentally split those literals as arithmetic/bitwise expressions.
        quoted = [False] * len(expr)
        in_string = False
        escape = False
        for q, ch in enumerate(expr):
            if in_string:
                quoted[q] = True
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                quoted[q] = True

        # Search right-to-left for left-associative operators so recursion preserves grouping.
        i = len(expr) - 1
        while i >= 0:
            if quoted[i]:
                i -= 1
                continue
            ch = expr[i]
            if ch == ')': pdepth += 1
            elif ch == '(': pdepth = max(0, pdepth-1)
            elif ch == ']': bdepth += 1
            elif ch == '[': bdepth = max(0, bdepth-1)
            elif ch == '}': cdepth += 1
            elif ch == '{': cdepth = max(0, cdepth-1)
            if pdepth == 0 and bdepth == 0 and cdepth == 0:
                for op in operators:
                    j = i-len(op)+1
                    if j >= 0 and not any(quoted[j:i+1]) and expr[j:i+1] == op:
                        # Avoid treating unary +/- as binary.
                        if op in ('+','-') and (j == 0 or expr[j-1] in '([?:,+-*/&|!<>='):
                            continue
                        return expr[:j].strip(), op, expr[i+1:].strip()
            i -= 1
        return None

    @staticmethod
    def _split_top_level_bitwise(expr: str, operator: str) -> Optional[tuple[str, str, str]]:
        """Split a top-level Verilog bitwise &, ^, or | expression.

        The generic binary splitter is intentionally permissive and is also used
        for multi-character operators.  For single-character bitwise operators
        we must explicitly avoid matching the characters inside && and || and
        any operator-like characters contained in Verilog string literals.
        """
        quoted = [False] * len(expr)
        in_string = False
        escape = False
        for q, ch in enumerate(expr):
            if in_string:
                quoted[q] = True
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                quoted[q] = True

        pdepth = bdepth = cdepth = 0
        i = len(expr) - 1
        while i >= 0:
            if quoted[i]:
                i -= 1
                continue
            ch = expr[i]
            if ch == ')':
                pdepth += 1
            elif ch == '(':
                pdepth = max(0, pdepth - 1)
            elif ch == ']':
                bdepth += 1
            elif ch == '[':
                bdepth = max(0, bdepth - 1)
            elif ch == '}':
                cdepth += 1
            elif ch == '{':
                cdepth = max(0, cdepth - 1)

            if pdepth == 0 and bdepth == 0 and cdepth == 0 and ch == operator:
                if operator in ('&', '|'):
                    prev_same = i > 0 and expr[i - 1] == operator
                    next_same = i + 1 < len(expr) and expr[i + 1] == operator
                    if prev_same or next_same:
                        i -= 1
                        continue
                return expr[:i].strip(), operator, expr[i + 1:].strip()
            i -= 1
        return None

    def _expr(self, expr: str, diagnostics: List[Diagnostic], signal_widths: dict[str, Optional[int]]) -> str:
        expr = self._strip_outer_parens(expr.strip())

        int_slice = re.fullmatch(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*:\s*0\s*\]", expr, re.S)
        if int_slice and int_slice.group(1) in getattr(self, "_integer_signal_names", set()):
            name = int_slice.group(1)
            hi = int(int_slice.group(2))
            return f"std_logic_vector(to_unsigned({name}, {hi + 1}))"

        cm = re.fullmatch(r'"(.)"', expr, re.S)
        if cm:
            return f"std_logic_vector(to_unsigned({ord(cm.group(1))}, 8))"
        sm = re.fullmatch(r"\$signed\s*\(([^()]*)\)", expr, re.S)
        if sm:
            return self._expr(sm.group(1), diagnostics, signal_widths)

        # Synthesizable unary reduction operators. Baseline VHDL does not
        # provide portable unary AND/OR/XOR reduction operators, so these lower
        # through small architecture-local helper functions emitted only when
        # needed.
        red = re.fullmatch(r"([&|^])\s*(.+)", expr, re.S)
        if red:
            op, operand = red.groups()
            rendered = self._expr(operand.strip(), diagnostics, signal_widths)
            width = self._raw_expr_width(operand.strip(), signal_widths)
            if width == 1:
                return rendered
            helper = {"&": "reduce_and", "|": "reduce_or", "^": "reduce_xor"}[op]
            return f"{helper}({rendered})"

        # User-function calls are context-sized by their formal arguments in Verilog.
        # VHDL requires each actual argument to already match the formal type.
        call = self._whole_call_parts(expr)
        if call:
            fname, arg_text = call
            sp = getattr(self, "_subprogram_map", {}).get(fname)
            if sp is not None and sp.kind == "function":
                raw_args = VerilogSubsetParser._split_top_level_commas(arg_text) if arg_text.strip() else []
                if len(raw_args) != len(sp.ports):
                    diagnostics.append(Diagnostic("error", f"Function {fname} expects {len(sp.ports)} argument(s), got {len(raw_args)}."))
                    return expr
                rendered_args = []
                for raw_arg, formal in zip(raw_args, sp.ports):
                    target_width = self._constant_width(formal.width)
                    rendered_args.append(self._assignment_rhs(raw_arg.strip(), target_width, diagnostics, signal_widths))
                return f"{fname}({', '.join(rendered_args)})"
        # General replication concatenation: {COUNT{value}}.
        # COUNT must be an integer expression in the supported subset.
        rep = re.fullmatch(r"\{\s*([^{}]+?)\s*\{\s*(.+)\s*\}\s*\}", expr, re.S)
        if rep:
            count_raw, value_raw = rep.groups()
            if not self._is_integer_expr(count_raw.strip()):
                diagnostics.append(Diagnostic(
                    "error",
                    f"Replication count must be an integer expression: {count_raw.strip()}"
                ))
                return expr
            count_vhdl = self._integer_expr(count_raw.strip(), diagnostics, signal_widths)
            value_raw = value_raw.strip()
            value_vhdl = self._expr(value_raw, diagnostics, signal_widths)
            value_width = self._raw_expr_width(value_raw, signal_widths)
            if value_width == 1:
                value_vhdl = f"std_logic_vector'(0 => {value_vhdl})"
            return f"repeat_slv({value_vhdl}, {count_vhdl})"

        # One-bit sign extension replication used heavily in synthesizable RTL.
        rm = re.fullmatch(r"\{\{1\{([^{}]+)\}\}\s*,\s*(.+)\}", expr, re.S)
        if rm:
            return f"{self._expr(rm.group(1), diagnostics, signal_widths)} & {self._expr(rm.group(2), diagnostics, signal_widths)}"

        split_ar = self._split_top_level_binary(expr, ['>>>'])
        if split_ar:
            a, _op, b = split_ar
            va = self._expr(a, diagnostics, signal_widths)
            b = b.strip()
            if re.fullmatch(r"\d+", b):
                amount = str(int(b))
            elif self._is_integer_expr(b):
                amount = self._integer_expr(b, diagnostics, signal_widths)
            else:
                vb = self._expr(b, diagnostics, signal_widths)
                wb = self._raw_expr_width(b, signal_widths)
                if wb == 1:
                    amount = f"to_integer(unsigned'(0 => {vb}))"
                else:
                    amount = f"to_integer(unsigned({vb}))"
            return f"std_logic_vector(shift_right(signed({va}), {amount}))"

        ternary = self._split_ternary(expr)
        if ternary:
            cond, yes, no = ternary
            return f"{self._expr(yes, diagnostics, signal_widths)} when {self._condition(cond, diagnostics, signal_widths)} else {self._expr(no, diagnostics, signal_widths)}"

        # Shifts bind tighter than comparisons and are common in graphics/address math.
        # numeric_std shift_left/shift_right accept a NATURAL shift count, so a
        # Verilog vector shift amount can be lowered deterministically through
        # to_integer(unsigned(...)).  This covers synthesizable constructs such
        # as one-hot generation: 16'b1 << key_code.
        split = self._split_top_level_binary(expr, ['>>','<<'])
        if split:
            a, op, b = split
            va = self._expr(a, diagnostics, signal_widths)
            fn = "shift_right" if op == ">>" else "shift_left"
            b = b.strip()
            if re.fullmatch(r"\d+", b):
                amount = str(int(b))
            elif self._is_integer_expr(b):
                amount = self._integer_expr(b, diagnostics, signal_widths)
            else:
                vb = self._expr(b, diagnostics, signal_widths)
                wb = self._raw_expr_width(b, signal_widths)
                if wb == 1:
                    amount = f"to_integer(unsigned'(0 => {vb}))"
                else:
                    amount = f"to_integer(unsigned({vb}))"
            return f"std_logic_vector({fn}(unsigned({va}), {amount}))"

        # Logical expressions in value context are normally handled by
        # _assignment_rhs/_condition. Preserve them here as booleans when nested.
        for ops, word in [(['||'], 'or'), (['&&'], 'and')]:
            split = self._split_top_level_binary(expr, ops)
            if split:
                a, _op, b = split
                return f"({self._condition(a, diagnostics, signal_widths)}) {word} ({self._condition(b, diagnostics, signal_widths)})"

        # Comparisons. Verilog freely compares vectors with unsized integer
        # parameters and also auto-extends different vector widths. Make both
        # conversions explicit for VHDL numeric_std.
        split = self._split_top_level_binary(expr, ['==','!=','<=','>=','<','>'])
        if split:
            a, op, b = split
            cmpop = '=' if op == '==' else '/=' if op == '!=' else op
            if self._is_integer_expr(a) and self._is_integer_expr(b):
                return f"{self._integer_expr(a, diagnostics, signal_widths)} {cmpop} {self._integer_expr(b, diagnostics, signal_widths)}"
            wa, wb = self._raw_expr_width(a, signal_widths), self._raw_expr_width(b, signal_widths)
            va, vb = self._expr(a, diagnostics, signal_widths), self._expr(b, diagnostics, signal_widths)
            # Scalar std_logic comparisons do not use numeric_std conversions.
            if wa == 1 and wb == 1:
                return f"{va} {cmpop} {vb}"
            if wa is not None or wb is not None:
                width = max(w for w in (wa, wb) if w is not None)
                if self._is_signed_expr(a) or self._is_signed_expr(b):
                    sa = self._as_signed(a, va, width, signal_widths)
                    sb = self._as_signed(b, vb, width, signal_widths)
                    return f"{sa} {cmpop} {sb}"
                ua = self._as_unsigned(a, va, width, signal_widths)
                ub = self._as_unsigned(b, vb, width, signal_widths)
                return f"{ua} {cmpop} {ub}"
            # A vector with a symbolic/generic width is represented by None in the
            # width map. Compare it to an integer using the vector's own 'length.
            a_ident = re.fullmatch(r"[A-Za-z_]\w*", a)
            b_ident = re.fullmatch(r"[A-Za-z_]\w*", b)
            if a_ident and signal_widths.get(a) is None and self._is_integer_expr(b):
                return f"unsigned({va}) {cmpop} to_unsigned({self._integer_expr(b, diagnostics, signal_widths)}, {a}'length)"
            if b_ident and signal_widths.get(b) is None and self._is_integer_expr(a):
                return f"to_unsigned({self._integer_expr(a, diagnostics, signal_widths)}, {b}'length) {cmpop} unsigned({vb})"
            return f"{va} {cmpop} {vb}"

        # Bitwise OR / XOR / AND.  These bind more weakly than arithmetic,
        # so recursively translate each operand before applying the VHDL
        # std_logic/std_logic_vector operator.
        #
        # Example from RV32I JALR address alignment:
        #   (rs1_data + imm_i) & 32'hFFFF_FFFE
        # must become a typed vector addition first, then vector AND.
        for bitop, vhdl_op in [('|', 'or'), ('^', 'xor'), ('&', 'and')]:
            split = self._split_top_level_bitwise(expr, bitop)
            if split:
                a, _op, b = split
                va = self._expr(a, diagnostics, signal_widths)
                vb = self._expr(b, diagnostics, signal_widths)
                return f"({va}) {vhdl_op} ({vb})"

        # Verilog *, /, and % share one precedence level and associate left-to-right.
        # Searching right-to-left here preserves that grouping recursively:
        #   A * 2 / 3  ->  (A * 2) / 3
        split = self._split_top_level_binary(expr, ['*','/','%'])
        if split:
            a, op, b = split
            if self._is_integer_expr(a) and self._is_integer_expr(b):
                ia = self._integer_expr(a, diagnostics, signal_widths)
                ib = self._integer_expr(b, diagnostics, signal_widths)
                vop = "mod" if op == "%" else op
                return f"({ia} {vop} {ib})"

            va = self._expr(a, diagnostics, signal_widths)
            vb = self._expr(b, diagnostics, signal_widths)

            if op == '*':
                signed_mul = self._is_signed_expr(a) or self._is_signed_expr(b)
                if self._is_integer_expr(b):
                    numeric = "signed" if signed_mul else "unsigned"
                    return f"std_logic_vector({numeric}({va}) * {self._integer_expr(b, diagnostics, signal_widths)})"
                if self._is_integer_expr(a):
                    numeric = "signed" if signed_mul else "unsigned"
                    return f"std_logic_vector({numeric}({vb}) * {self._integer_expr(a, diagnostics, signal_widths)})"
                if signed_mul:
                    return f"std_logic_vector(signed({va}) * signed({vb}))"
                return f"std_logic_vector(unsigned({va}) * unsigned({vb}))"

            if op == '/':
                if self._is_integer_expr(b):
                    return f"std_logic_vector(unsigned({va}) / {self._integer_expr(b, diagnostics, signal_widths)})"
                return f"std_logic_vector(unsigned({va}) / unsigned({vb}))"

            # Verilog remainder (%) maps to VHDL numeric_std rem for unsigned math.
            if self._is_integer_expr(b):
                return f"std_logic_vector(unsigned({va}) rem {self._integer_expr(b, diagnostics, signal_widths)})"
            return f"std_logic_vector(unsigned({va}) rem unsigned({vb}))"

        # Integer-only localparam arithmetic stays integer. Mixed/vector arithmetic
        # uses explicit unsigned resizing to model Verilog's width coercion.
        split = self._split_top_level_binary(expr, ['+','-'])
        if split:
            a, op, b = split
            if self._is_integer_expr(a) and self._is_integer_expr(b):
                return f"({self._integer_expr(a, diagnostics, signal_widths)} {op} {self._integer_expr(b, diagnostics, signal_widths)})"
            wa, wb = self._raw_expr_width(a, signal_widths), self._raw_expr_width(b, signal_widths)
            va, vb = self._expr(a, diagnostics, signal_widths), self._expr(b, diagnostics, signal_widths)
            # Preserve the width of a generic/symbolic vector operand. Its width-map
            # entry is None because it cannot be reduced at translation time.
            a_symbolic = bool(re.fullmatch(r"[A-Za-z_]\w*", a) and a in signal_widths and signal_widths.get(a) is None)
            b_symbolic = bool(re.fullmatch(r"[A-Za-z_]\w*", b) and b in signal_widths and signal_widths.get(b) is None)
            bit_b = self._bit_literal_value(b)
            bit_a = self._bit_literal_value(a)
            if a_symbolic and (self._is_integer_expr(b) or bit_b is not None):
                rhs_nat = self._integer_expr(b, diagnostics, signal_widths) if self._is_integer_expr(b) else str(bit_b)
                return f"std_logic_vector(unsigned({va}) {op} {rhs_nat})"
            if b_symbolic and op == '+' and (self._is_integer_expr(a) or bit_a is not None):
                lhs_nat = self._integer_expr(a, diagnostics, signal_widths) if self._is_integer_expr(a) else str(bit_a)
                return f"std_logic_vector(unsigned({vb}) + {lhs_nat})"
            width = max(w for w in (wa, wb) if w is not None) if (wa is not None or wb is not None) else None
            if width is None:
                # Symbolic/generic vector widths (e.g. [$clog2(DIV)-1:0]) cannot
                # be reduced to an integer here. numeric_std supports unsigned +/-
                # NATURAL, which preserves the generic vector's length.
                bit_b = self._bit_literal_value(b)
                bit_a = self._bit_literal_value(a)
                if bit_b is not None and not self._is_integer_expr(a):
                    return f"std_logic_vector(unsigned({va}) {op} {bit_b})"
                if bit_a is not None and not self._is_integer_expr(b) and op == '+':
                    return f"std_logic_vector(unsigned({vb}) + {bit_a})"
                if self._is_integer_expr(b) and not self._is_integer_expr(a):
                    return f"std_logic_vector(unsigned({va}) {op} {self._integer_expr(b, diagnostics, signal_widths)})"
                if self._is_integer_expr(a) and not self._is_integer_expr(b) and op == '+':
                    return f"std_logic_vector(unsigned({vb}) + {self._integer_expr(a, diagnostics, signal_widths)})"
                diagnostics.append(Diagnostic("error", f"Could not determine arithmetic width: {expr}"))
                return expr
            if self._is_signed_expr(a) or self._is_signed_expr(b):
                sa = self._as_signed(a, va, width, signal_widths)
                sb = self._as_signed(b, vb, width, signal_widths)
                return f"std_logic_vector({sa} {op} {sb})"
            ua = self._as_unsigned(a, va, width, signal_widths)
            ub = self._as_unsigned(b, vb, width, signal_widths)
            return f"std_logic_vector({ua} {op} {ub})"

        expr = self._translate_selects(expr, signal_widths)

        # Concatenation {a, b, c} -> a & b & c. Replication parts are
        # translated recursively by _expr().
        if expr.startswith('{') and expr.endswith('}'):
            inner = expr[1:-1].strip()
            parts = VerilogSubsetParser._split_top_level_commas(inner)
            rendered = []
            for p in parts:
                pm = re.fullmatch(r"(\d+)'[bB]([01_]+)", p.strip())
                if pm:
                    w = int(pm.group(1)); bits = pm.group(2).replace('_','').zfill(w)[-w:]
                    rendered.append(f"'{bits}'" if w == 1 else f'"{bits}"')
                else:
                    rendered.append(self._expr(p, diagnostics, signal_widths))
            joined = " & ".join(rendered)

            # Verilog concatenation always has a packed-vector result. VHDL's '&'
            # is heavily overloaded, so make the result type explicit. This is
            # especially important when the concat feeds arithmetic or mixes
            # literals, ordinary vectors, and memory elements.
            return "std_logic_vector'(" + joined + ")"

        m = re.fullmatch(r"([A-Za-z_]\w*)\s*([+-])\s*(\d+)'[dD]([0-9_]+)", expr)
        if m:
            sig, op, _lit_width, digits = m.groups()
            value = int(digits.replace('_', ''))
            return f"std_logic_vector(unsigned({sig}) {op} {value})"

        def lit(m: re.Match) -> str:
            width = int(m.group(1)); base = m.group(2).lower(); digits = m.group(3).replace('_', '')

            # Four-state binary literals map naturally to std_logic/std_logic_vector.
            # This is especially important for synthesizable open-drain/tri-state RTL:
            #     assign sda = pull_low ? 1'b0 : 1'bz;
            # becomes:
            #     sda <= '0' when ... else 'Z';
            if base == 'b' and re.search(r"[xXzZ]", digits):
                bits = digits.upper()
                if len(bits) < width:
                    pad = bits[0] if bits and bits[0] in "XZ" else "0"
                    bits = pad * (width - len(bits)) + bits
                bits = bits[-width:]
                if width == 1:
                    return f"'{bits}'"
                return f'"{bits}"'

            if re.search(r"[xXzZ]", digits):
                diagnostics.append(Diagnostic(
                    "error",
                    f"X/Z literal is currently supported for binary literals only: {m.group(0)}"
                ))
                return m.group(0)
            try:
                value = int(digits, {'b': 2, 'd': 10, 'h': 16, 'o': 8}[base])
            except ValueError:
                diagnostics.append(Diagnostic("error", f"Could not parse literal: {m.group(0)}"))
                return m.group(0)
            if width == 1:
                return f"'{value & 1}'"

            # VHDL INTEGER is typically limited to 31-bit positive values in
            # Vivado.  Preserve wide Verilog constants exactly as bit strings
            # instead of creating an overflowing to_unsigned(integer, width).
            if value > 2_147_483_647:
                bits = format(value & ((1 << width) - 1), f"0{width}b")
                return f'"{bits}"'

            return f"std_logic_vector(to_unsigned({value}, {width}))"

        expr = re.sub(r"\b(\d+)'(?:[sS])?([bBdDhHoO])([0-9a-fA-F_xXzZ]+)", lit, expr)
        expr = expr.replace("&&", " and ").replace("||", " or ")
        expr = re.sub(r"(?<![=!<>])==(?![=])", " = ", expr)
        expr = re.sub(r"!=(?!=)", " /= ", expr)
        # logical ! that is not part of !=
        expr = re.sub(r"!(?!=)", "not ", expr)
        expr = re.sub(r"~", "not ", expr)

        # Translate common bitwise operators when they occur between operands.
        expr = re.sub(r"(?<!&)&(?!&)", " and ", expr)
        expr = re.sub(r"(?<!\|)\|(?!\|)", " or ", expr)
        expr = re.sub(r"\^", " xor ", expr)
        expr = re.sub(r"\s+", " ", expr).strip()

        m = re.fullmatch(r"([A-Za-z_]\w*)\s*([+-])\s*(\d+)", expr)
        if m:
            sig, op, val = m.groups()
            if signal_widths.get(sig, 1) != 1:
                return f"std_logic_vector(unsigned({sig}) {op} {val})"
        if '?' in expr or re.search(r"(?<![<>])(?:>>|<<)(?![<>])", expr):
            diagnostics.append(Diagnostic("error", f"Unsupported expression survived translation: {expr}"))
        return expr

    def _translate_selects(self, expr: str, signal_widths: Optional[dict[str, Optional[int]]] = None) -> str:
        """Translate Verilog packed selects to VHDL indexing/slicing.

        VHDL requires an INTEGER index. Verilog permits vector expressions such as
        font_bits[7-col], where col itself is a packed vector. Convert vector
        identifiers used in a dynamic index to to_integer(unsigned(...)).
        """
        widths = signal_widths or {}
        expr = re.sub(r"\b([A-Za-z_]\w*)\s*\[\s*([^\]:]+)\s*:\s*([^\]]+)\s*\]", r"\1(\2 downto \3)", expr)

        def bit_select(m: re.Match) -> str:
            name = m.group(1)
            idx = m.group(2).strip()

            # Array/bit indexes are INTEGER expressions in VHDL.  Convert
            # Verilog sized literals used inside an index to their numeric
            # value before the general literal translator can turn 1'b1 into
            # the std_logic literal '1'.  Example:
            #     calibration[cal_index + 1'b1]
            # becomes:
            #     calibration(to_integer(unsigned(cal_index)) + 1)
            def repl_sized_index_literal(lm: re.Match) -> str:
                base = lm.group(2).lower()
                digits = lm.group(3).replace("_", "")
                try:
                    return str(int(digits, {'b':2, 'd':10, 'h':16, 'o':8}[base]))
                except ValueError:
                    return lm.group(0)

            idx = re.sub(
                r"(\d+)'(?:[sS])?([bBdDhHoO])([0-9a-fA-F_]+)",
                repl_sized_index_literal,
                idx,
            )

            def repl_id(im: re.Match) -> str:
                ident = im.group(0)
                width = widths.get(ident)
                if width is not None and width > 1:
                    return f"to_integer(unsigned({ident}))"
                return ident

            idx_vhdl = re.sub(r"\b[A-Za-z_]\w*\b", repl_id, idx)
            return f"{name}({idx_vhdl})"

        expr = re.sub(r"\b([A-Za-z_]\w*)\s*\[\s*([^\]]+)\s*\]", bit_select, expr)

        # Apply deterministic Verilog->VHDL identifier renames first.
        for ident, emitted in getattr(self, "_source_name_map", {}).items():
            expr = re.sub(
                rf"\b{re.escape(ident)}\b",
                lambda _m, emitted=emitted: emitted,
                expr,
            )

        # Escape only source identifiers known to this expression's width map.
        # This avoids touching VHDL keywords introduced by the translation itself
        # (for example the generated "downto" in a slice).
        for ident in widths:
            if ident.lower() in self._VHDL_RESERVED:
                expr = re.sub(
                    rf"\b{re.escape(ident)}\b",
                    lambda _m, ident=ident: self._source_ident(ident),
                    expr,
                )
        return expr

    @staticmethod
    def _simple_index_expr(expr: str) -> str:
        expr = expr.strip()
        expr = re.sub(r"\$clog2\s*\(", "clog2(", expr)
        return expr


class _ProceduralTranslator:
    def __init__(self, gen: VHDLGenerator, text: str, diagnostics: List[Diagnostic], reg_ports: List[Port], wire_proxy_ports: List[Port], signal_widths: dict[str, Optional[int]], base_line: int, sequential: bool, process_variables: Optional[set[str]] = None):
        self.gen = gen
        self.text = text
        self.diagnostics = diagnostics
        self.reg_ports = reg_ports
        self.wire_proxy_ports = wire_proxy_ports
        self.signal_widths = signal_widths
        self.base_line = base_line
        self.sequential = sequential
        self.process_variables = process_variables or set()
        self.pos = 0

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _word_at(self, word: str) -> bool:
        self.skip_ws()
        end = self.pos + len(word)
        if self.text[self.pos:end] != word:
            return False
        before_ok = self.pos == 0 or not (self.text[self.pos-1].isalnum() or self.text[self.pos-1] == '_')
        after_ok = end >= len(self.text) or not (self.text[end].isalnum() or self.text[end] == '_')
        return before_ok and after_ok

    def _consume_word(self, word: str) -> None:
        if not self._word_at(word):
            raise ValueError(f"Expected '{word}' in procedural block")
        self.pos += len(word)

    def _balanced_parens(self) -> str:
        self.skip_ws()
        if self.pos >= len(self.text) or self.text[self.pos] != '(':
            raise ValueError("Expected '(' in procedural statement")
        start = self.pos + 1
        depth = 1
        i = start
        while i < len(self.text):
            ch = self.text[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    out = self.text[start:i]
                    self.pos = i + 1
                    return out
            i += 1
        raise ValueError("Unbalanced parentheses in procedural statement")

    def _read_to_semicolon(self) -> str:
        self.skip_ws()
        start = self.pos
        depth = 0
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch in '({[':
                depth += 1
            elif ch in ')}]':
                depth = max(0, depth - 1)
            elif ch == ';' and depth == 0:
                self.pos += 1
                return self.text[start:self.pos]
            self.pos += 1
        raise ValueError("Missing semicolon in procedural assignment")

    def parse_block(self, stop_tokens: set[str]) -> List[str]:
        out: List[str] = []
        while True:
            self.skip_ws()
            if self.pos >= len(self.text):
                break
            if any(self._word_at(tok) for tok in stop_tokens):
                break
            if self._word_at('if'):
                out.extend(self.parse_if())
            elif self._word_at('case'):
                out.extend(self.parse_case())
            elif self._word_at('for'):
                out.extend(self.parse_for())
            elif self._word_at('begin'):
                self._consume_word('begin')
                out.extend(self.parse_block({'end'}))
                self._consume_word('end')
            else:
                stmt = self._read_to_semicolon()
                callm = re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*)\)\s*;", stmt.strip(), re.S)
                if callm and callm.group(1) in getattr(self.gen, "_task_names", set()):
                    tname = callm.group(1)
                    raw_args = VerilogSubsetParser._split_top_level_commas(callm.group(2))
                    sp = getattr(self.gen, "_subprogram_map", {}).get(tname)
                    args = []
                    for idx, a in enumerate(raw_args):
                        mapped = self.gen._map_rhs(a.strip(), self.reg_ports, self.wire_proxy_ports)
                        if sp is not None and idx < len(sp.ports):
                            tw = self.gen._constant_width(sp.ports[idx].width)
                            args.append(self.gen._assignment_rhs(mapped, tw, self.diagnostics, self.signal_widths))
                        else:
                            args.append(self.gen._expr(mapped, self.diagnostics, self.signal_widths))
                    for outer_name in getattr(self.gen, "_task_outer_writes", {}).get(tname, []):
                        actual = outer_name
                        for p in self.reg_ports:
                            if actual == p.name:
                                actual = p.name + "_reg"
                                break
                        for p in self.wire_proxy_ports:
                            if actual == p.name:
                                actual = p.name + "_int"
                                break
                        args.append(actual)
                    out.append(f"{tname}({', '.join(args)});")
                else:
                    out.append(self.gen._translate_single_assignment(stmt, self.diagnostics, self.reg_ports, self.wire_proxy_ports, self.signal_widths, self.base_line, self.sequential, self.process_variables))
        return out

    def _parse_statement_or_begin(self) -> List[str]:
        self.skip_ws()
        if self._word_at('begin'):
            self._consume_word('begin')
            lines = self.parse_block({'end'})
            self._consume_word('end')
            return lines
        if self._word_at('if'):
            return self.parse_if()
        if self._word_at('case'):
            return self.parse_case()
        if self._word_at('for'):
            return self.parse_for()
        stmt = self._read_to_semicolon()
        return [self.gen._translate_single_assignment(stmt, self.diagnostics, self.reg_ports, self.wire_proxy_ports, self.signal_widths, self.base_line, self.sequential, self.process_variables)]

    def parse_for(self) -> List[str]:
        """Translate the common synthesizable counted procedural for-loop.

        Supported form:
            for (i = START; i < STOP; i = i + 1)
                statement;
        or the same with begin/end.

        The VHDL loop parameter is an integer, so dynamic array indexes using
        that name are emitted directly rather than converted as vectors.
        """
        self._consume_word('for')
        hdr = self._balanced_parens()
        m = re.fullmatch(
            r"\s*([A-Za-z_]\w*)\s*=\s*(\d+)\s*;\s*"
            r"\1\s*<\s*(\d+)\s*;\s*"
            r"\1\s*=\s*\1\s*\+\s*1\s*",
            hdr
        )
        if not m:
            raise ValueError(f"Unsupported for-loop form: for ({hdr})")

        var, start, stop = m.group(1), int(m.group(2)), int(m.group(3))

        old_names = set(getattr(self.gen, "_integer_local_names", set()))
        self.gen._integer_local_names = old_names | {var}
        try:
            body = self._parse_statement_or_begin()
        finally:
            self.gen._integer_local_names = old_names

        return (
            [f"for {var} in {start} to {stop - 1} loop"]
            + ["    " + line for line in body]
            + ["end loop;"]
        )

    def parse_if(self) -> List[str]:
        self._consume_word('if')
        cond = self._balanced_parens()
        cond = self.gen._map_rhs(cond, self.reg_ports, self.wire_proxy_ports)
        yes = self._parse_statement_or_begin()
        out = [f"if {self.gen._condition(cond, self.diagnostics, self.signal_widths)} then"]
        out.extend('    ' + x for x in yes)
        self.skip_ws()
        if self._word_at('else'):
            self._consume_word('else')
            self.skip_ws()
            if self._word_at('if'):
                nested = self.parse_if()
                # Turn nested VHDL 'if' after else into elsif for clean output.
                first = nested[0]
                if first.startswith('if ') and first.endswith(' then'):
                    out.append('elsif ' + first[3:])
                    out.extend(nested[1:-1])
                    out.append('end if;')
                else:
                    out.append('else')
                    out.extend('    ' + x for x in nested)
                    out.append('end if;')
                return out
            no = self._parse_statement_or_begin()
            out.append('else')
            out.extend('    ' + x for x in no)
        out.append('end if;')
        return out

    def parse_case(self) -> List[str]:
        self._consume_word('case')
        expr = self._balanced_parens()
        expr = self.gen._map_rhs(expr, self.reg_ports, self.wire_proxy_ports)
        selector_width = self.signal_widths.get(expr) if re.fullmatch(r"[A-Za-z_]\w*", expr) else None
        out = [f"case {self.gen._expr(expr, self.diagnostics, self.signal_widths)} is"]
        has_others = False
        while True:
            self.skip_ws()
            if self._word_at('endcase'):
                self._consume_word('endcase')
                break
            if self.pos >= len(self.text):
                raise ValueError("Missing endcase")

            # Read case choice up to ':' at top level.
            start = self.pos
            depth = 0
            in_string = False
            while self.pos < len(self.text):
                ch = self.text[self.pos]
                if ch == '"':
                    in_string = not in_string
                elif not in_string and ch in '({[':
                    depth += 1
                elif not in_string and ch in ')}]':
                    depth = max(0, depth - 1)
                elif not in_string and ch == ':' and depth == 0:
                    break
                self.pos += 1
            if self.pos >= len(self.text):
                raise ValueError("Malformed case item")
            choice = self.text[start:self.pos].strip()
            self.pos += 1

            if choice == 'default':
                vchoice = 'others'
                has_others = True
            else:
                choices = [c.strip() for c in VerilogSubsetParser._split_top_level_commas(choice)]
                vals = []
                for c in choices:
                    if selector_width and re.fullmatch(r"\d+", c):
                        value = int(c)
                        if selector_width == 1:
                            vals.append(f"'{value & 1}'")
                        else:
                            vals.append('"' + format(value & ((1 << selector_width) - 1), f'0{selector_width}b') + '"')
                    else:
                        vals.append(self._case_choice(c))
                vchoice = ' | '.join(vals)
            lines = self._parse_statement_or_begin()
            out.append(f"    when {vchoice} =>")
            if lines:
                out.extend('        ' + x for x in lines)
            else:
                out.append('        null;')

        # VHDL case statements must cover every possible selector value.
        # A Verilog case without default simply performs no action for unmatched
        # values, so preserve that behavior with an explicit null others arm.
        if not has_others:
            out.append('    when others =>')
            out.append('        null;')

        out.append('end case;')
        return out

    def _case_choice(self, choice: str) -> str:
        choice = choice.strip()
        cm = re.fullmatch(r'"(.)"', choice, re.S)
        if cm:
            return '"' + format(ord(cm.group(1)), '08b') + '"'
        m = re.fullmatch(r"(\d+)'(?:[sS])?([bBdDhHoO])([0-9a-fA-F_]+)", choice)
        if m:
            width = int(m.group(1))
            base = m.group(2).lower()
            digits = m.group(3).replace('_', '')
            value = int(digits, {'b': 2, 'd': 10, 'h': 16, 'o': 8}[base])
            bits = format(value & ((1 << width) - 1), f'0{width}b')
            if width == 1:
                return f"'{bits}'"
            return f'"{bits}"'
        return self.gen._expr(choice, self.diagnostics, self.signal_widths)


class _HelperProceduralTranslator(_ProceduralTranslator):
    """Procedural translator for architecture-local VHDL functions/procedures."""
    def __init__(self, gen, text, diagnostics, signal_widths, base_line, subprogram, is_task):
        super().__init__(gen, text, diagnostics, [], [], signal_widths, base_line, False)
        self.subprogram = subprogram
        self.is_task = is_task
        self.local_names = {v.name for v in subprogram.locals}
        self.output_names = {p.name for p in subprogram.ports if p.direction in ('output','inout')}

    def _translate_helper_stmt(self, stmt: str) -> str:
        stmt=stmt.strip()
        callm=re.fullmatch(r"([A-Za-z_]\w*)\s*\((.*)\)\s*;",stmt,re.S)
        if callm and callm.group(1) in getattr(self.gen,'_task_names',set()):
            tname=callm.group(1)
            raw_args=VerilogSubsetParser._split_top_level_commas(callm.group(2))
            sp=getattr(self.gen,'_subprogram_map',{}).get(tname)
            args=[]
            for idx,a in enumerate(raw_args):
                if sp is not None and idx < len(sp.ports):
                    tw=self.gen._constant_width(sp.ports[idx].width)
                    args.append(self.gen._assignment_rhs(a.strip(),tw,self.diagnostics,self.signal_widths))
                else:
                    args.append(self.gen._expr(a.strip(),self.diagnostics,self.signal_widths))
            return f"{tname}({', '.join(args)});"
        m=re.match(r"(.+?)\s*(<=|=)\s*(.+?)\s*;?$",stmt,re.S)
        if not m:
            self.diagnostics.append(Diagnostic('error',f'Unsupported helper statement: {stmt}',self.base_line)); return '-- ERROR helper statement'
        lhs,_op,rhs=m.groups(); lhs=lhs.strip(); rhs=rhs.strip()
        base=re.match(r"([A-Za-z_]\w*)",lhs)
        base_name=base.group(1) if base else lhs
        lhs_v=self.gen._translate_selects(lhs, self.signal_widths)
        if self.subprogram.kind=='function' and base_name==self.subprogram.name:
            lhs_v=self.subprogram.name+'_result'+lhs_v[len(base_name):]
            kind='var'
        elif base_name in self.local_names:
            kind='var'
        elif base_name in self.output_names:
            kind='signal'
        else:
            kind='signal'
        if self.subprogram.kind == 'function' and base_name == self.subprogram.name:
            tw = self.gen._constant_width(self.subprogram.return_width)
        else:
            tw = self.gen._lhs_width(lhs_v,self.signal_widths)
        op_v = '<=' if kind=='signal' else ':='
        # VHDL-2008 permits conditional expressions in sequential statements,
        # but ordinary Vivado projects may import generated files as baseline
        # VHDL. Lower Verilog ?: into an explicit if/else here.
        ternary=self.gen._split_ternary(rhs)
        if ternary:
            cond,yes,no=ternary
            c=self.gen._condition(cond,self.diagnostics,self.signal_widths)
            y=self.gen._assignment_rhs(yes,tw,self.diagnostics,self.signal_widths)
            n=self.gen._assignment_rhs(no,tw,self.diagnostics,self.signal_widths)
            return f"if {c} then\n    {lhs_v} {op_v} {y};\nelse\n    {lhs_v} {op_v} {n};\nend if;"
        if tw == 1 and self.gen._looks_boolean(rhs):
            c=self.gen._condition(rhs,self.diagnostics,self.signal_widths)
            return f"if {c} then\n    {lhs_v} {op_v} '1';\nelse\n    {lhs_v} {op_v} '0';\nend if;"
        rv=self.gen._assignment_rhs(rhs,tw,self.diagnostics,self.signal_widths)
        return f"{lhs_v} {op_v} {rv};"

    def parse_block(self, stop_tokens:set[str])->List[str]:
        out=[]
        while True:
            self.skip_ws()
            if self.pos>=len(self.text): break
            if any(self._word_at(tok) for tok in stop_tokens): break
            if self._word_at('if'): out.extend(self.parse_if())
            elif self._word_at('case'): out.extend(self.parse_case())
            elif self._word_at('for'): out.extend(self.parse_for())
            elif self._word_at('begin'):
                self._consume_word('begin'); out.extend(self.parse_block({'end'})); self._consume_word('end')
            else:
                out.append(self._translate_helper_stmt(self._read_to_semicolon()))
        return out

    def _parse_statement_or_begin(self)->List[str]:
        self.skip_ws()
        if self._word_at('begin'):
            self._consume_word('begin'); lines=self.parse_block({'end'}); self._consume_word('end'); return lines
        if self._word_at('if'): return self.parse_if()
        if self._word_at('case'): return self.parse_case()
        if self._word_at('for'): return self.parse_for()
        return [self._translate_helper_stmt(self._read_to_semicolon())]

    def parse_for(self)->List[str]:
        self._consume_word('for'); hdr=self._balanced_parens()
        m=re.fullmatch(r"\s*([A-Za-z_]\w*)\s*=\s*(\d+)\s*;\s*\1\s*<\s*(\d+)\s*;\s*\1\s*=\s*\1\s*\+\s*1\s*",hdr)
        if not m: raise ValueError(f'Unsupported for-loop form: for ({hdr})')
        var,start,stop=m.group(1),int(m.group(2)),int(m.group(3))
        old_names = set(getattr(self.gen, "_integer_local_names", set()))
        self.gen._integer_local_names = old_names | {var}
        try:
            body=self._parse_statement_or_begin()
        finally:
            self.gen._integer_local_names = old_names
        return [f"for {var} in {start} to {stop-1} loop"]+['    '+x for x in body]+['end loop;']

    def parse_case(self)->List[str]:
        self._consume_word('case'); expr=self._balanced_parens().strip()
        expr_v=self.gen._expr(expr,self.diagnostics,self.signal_widths)
        selector_width=self.signal_widths.get(expr) if re.fullmatch(r"[A-Za-z_]\w*",expr) else None
        out=[f"case {expr_v} is"]
        while True:
            self.skip_ws()
            if self._word_at('endcase'): self._consume_word('endcase'); break
            if self.pos>=len(self.text): raise ValueError('Missing endcase')
            start=self.pos; depth=0
            in_string=False
            while self.pos<len(self.text):
                ch=self.text[self.pos]
                if ch=='"': in_string=not in_string
                elif not in_string and ch in '({[': depth+=1
                elif not in_string and ch in ')}]': depth=max(0,depth-1)
                elif not in_string and ch==':' and depth==0: break
                self.pos+=1
            choice=self.text[start:self.pos].strip(); self.pos+=1
            if choice=='default': vchoice='others'
            else:
                vals=[]
                for c in VerilogSubsetParser._split_top_level_commas(choice):
                    c=c.strip()
                    if selector_width and re.fullmatch(r"\d+",c):
                        vals.append("'{}'".format(int(c)&1) if selector_width==1 else '"'+format(int(c)&((1<<selector_width)-1),f'0{selector_width}b')+'"')
                    else: vals.append(self._case_choice(c))
                vchoice=' | '.join(vals)
            lines=self._parse_statement_or_begin(); out.append(f"    when {vchoice} =>")
            out.extend('        '+x for x in lines) if lines else out.append('        null;')
        out.append('end case;'); return out


class VerilogToVHDLTranslator:
    def __init__(self) -> None:
        self.parser = VerilogSubsetParser()
        self.generator = VHDLGenerator()

    def translate_text(self, text: str, source_path: Optional[Path] = None) -> TranslationResult:
        module, diagnostics = self.parser.parse(text, source_path)
        if module is None:
            return TranslationResult(None, "", diagnostics)
        vhdl = self.generator.generate(module, diagnostics)
        return TranslationResult(module, vhdl, diagnostics)

    def translate_file(self, path: Path) -> TranslationResult:
        return self.translate_text(path.read_text(encoding="utf-8"), path)
