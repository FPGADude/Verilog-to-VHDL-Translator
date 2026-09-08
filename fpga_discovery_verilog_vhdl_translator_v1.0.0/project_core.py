from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable

from translator_core import Diagnostic, Module, TranslationResult, VerilogToVHDLTranslator

KNOWN_VENDOR_EXTERNALS = {"xpm_memory_sprom", "STARTUPE2"}


@dataclass
class ProjectModule:
    path: Path
    result: TranslationResult

    @property
    def module(self) -> Module | None:
        return self.result.module


@dataclass
class HierarchyNode:
    module_name: str
    instance_name: str | None = None
    source_path: Path | None = None
    resolved: bool = True
    children: list["HierarchyNode"] = field(default_factory=list)


@dataclass
class ProjectResult:
    modules: dict[str, ProjectModule]
    roots: list[HierarchyNode]
    diagnostics: list[str]
    headers: list[Path] = field(default_factory=list)
    resources: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.modules) and all(pm.result.ok for pm in self.modules.values()) and not any(
            line.startswith("ERROR:") for line in self.diagnostics
        )

    @property
    def translated_count(self) -> int:
        return sum(1 for pm in self.modules.values() if pm.result.ok)


class ProjectTranslator:
    """Translate and analyze a group of one-module-per-file Verilog sources."""

    def __init__(self) -> None:
        self.translator = VerilogToVHDLTranslator()

    @staticmethod
    def _classify_paths(paths: Iterable[Path]) -> tuple[list[Path], list[Path], list[Path]]:
        all_paths = sorted({Path(p).resolve() for p in paths}, key=lambda p: p.name.lower())
        sources = [p for p in all_paths if p.suffix.lower() == ".v"]
        headers = [p for p in all_paths if p.suffix.lower() in {".vh", ".svh"}]
        resources = [p for p in all_paths if p.suffix.lower() not in {".v", ".vh", ".svh"}]
        return sources, headers, resources

    @staticmethod
    def _macro_replace(line: str, macros: dict[str, str]) -> str:
        def repl(m):
            name = m.group(1)
            return macros.get(name, m.group(0))
        return re.sub(r"`([A-Za-z_]\w*)", repl, line)

    def _preprocess_file(
        self,
        path: Path,
        selected_headers: dict[str, Path],
        macros: dict[str, str] | None = None,
        include_stack: list[Path] | None = None,
    ) -> tuple[str, list[str]]:
        macros = macros if macros is not None else {}
        include_stack = include_stack or []
        diagnostics: list[str] = []

        resolved = path.resolve()
        if resolved in include_stack:
            chain = " -> ".join(p.name for p in include_stack + [resolved])
            return "", [f"ERROR: recursive `include detected: {chain}"]

        try:
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return "", [f"ERROR: {resolved.name}: could not read source/header: {exc}"]

        out: list[str] = []
        active_stack: list[tuple[bool, bool]] = []
        active = True

        for lineno, raw in enumerate(text.splitlines(), 1):
            stripped = raw.strip()

            m = re.match(r'`include\s+"([^"]+)"', stripped)
            if m:
                if not active:
                    out.append("")
                    continue
                inc_name = m.group(1)
                inc = selected_headers.get(inc_name.lower())
                if inc is None:
                    sibling = resolved.parent / inc_name
                    if sibling.exists():
                        inc = sibling
                if inc is None:
                    diagnostics.append(
                        f"ERROR: {resolved.name}:{lineno}: include file '{inc_name}' was not found. "
                        "Import the .vh file or place it beside the Verilog source."
                    )
                    out.append("")
                    continue
                included_text, inc_diags = self._preprocess_file(
                    inc, selected_headers, macros, include_stack + [resolved]
                )
                diagnostics.extend(inc_diags)
                out.append(included_text)
                continue

            m = re.match(r"`define\s+([A-Za-z_]\w*)(?:\s+(.*))?$", stripped)
            if m:
                if active:
                    name, value = m.groups()
                    macros[name] = (value or "1").strip()
                out.append("")
                continue

            m = re.match(r"`undef\s+([A-Za-z_]\w*)", stripped)
            if m:
                if active:
                    macros.pop(m.group(1), None)
                out.append("")
                continue

            m = re.match(r"`(ifdef|ifndef)\s+([A-Za-z_]\w*)", stripped)
            if m:
                kind, name = m.groups()
                cond = name in macros
                if kind == "ifndef":
                    cond = not cond
                active_stack.append((active, cond))
                active = active and cond
                out.append("")
                continue

            if re.match(r"`else\b", stripped):
                if not active_stack:
                    diagnostics.append(f"ERROR: {resolved.name}:{lineno}: unmatched `else.")
                else:
                    parent, cond = active_stack[-1]
                    active_stack[-1] = (parent, not cond)
                    active = parent and (not cond)
                out.append("")
                continue

            if re.match(r"`endif\b", stripped):
                if not active_stack:
                    diagnostics.append(f"ERROR: {resolved.name}:{lineno}: unmatched `endif.")
                else:
                    parent, _cond = active_stack.pop()
                    active = parent
                out.append("")
                continue

            if re.match(r"`timescale\b", stripped):
                out.append("")
                continue

            if not active:
                out.append("")
                continue

            replaced = self._macro_replace(raw, macros)

            # Do not diagnose backtick-looking text inside // comments.  This
            # matters for documentation comments that mention constructs such as
            # `define or `ifdef literally.
            code_for_unknown = replaced.split("//", 1)[0]
            unknown = re.search(r"`([A-Za-z_]\w*)", code_for_unknown)
            if unknown:
                diagnostics.append(
                    f"ERROR: {resolved.name}:{lineno}: unresolved Verilog macro/directive `{unknown.group(1)}."
                )
            out.append(replaced)

        if active_stack:
            diagnostics.append(f"ERROR: {resolved.name}: unterminated preprocessor conditional.")

        return "\n".join(out) + "\n", diagnostics

    def _prepare_project_inputs(
        self, paths: Iterable[Path]
    ) -> tuple[list[tuple[Path, str]], list[Path], list[Path], list[str]]:
        sources, headers, resources = self._classify_paths(paths)
        header_map = {p.name.lower(): p for p in headers}
        prepared: list[tuple[Path, str]] = []
        diags: list[str] = []
        for source in sources:
            text, pp_diags = self._preprocess_file(source, header_map, macros={})
            prepared.append((source, text))
            diags.extend(pp_diags)
        return prepared, headers, resources, diags

    def analyze_files(self, paths: Iterable[Path]) -> ProjectResult:
        """Parse Verilog and build hierarchy without generating any VHDL."""
        modules: dict[str, ProjectModule] = {}
        prepared, headers, resources, project_diags = self._prepare_project_inputs(paths)

        for path, text in prepared:
            try:
                module, diagnostics = self.translator.parser.parse(text, path)
                result = TranslationResult(module, "", diagnostics)
            except (OSError, UnicodeError) as exc:
                project_diags.append(f"ERROR: {path.name}: could not read source: {exc}")
                continue
            if result.module is None:
                details = "; ".join(d.format() for d in result.diagnostics) or "parse failed"
                project_diags.append(f"ERROR: {path.name}: {details}")
                continue
            name = result.module.name
            if name in modules:
                project_diags.append(
                    f"ERROR: duplicate module '{name}' in {path.name} and {modules[name].path.name}."
                )
                continue
            modules[name] = ProjectModule(path=path, result=result)

        result = self._finish_project(modules, project_diags)
        result.headers = headers
        result.resources = resources
        return result

    def translate_files(self, paths: Iterable[Path]) -> ProjectResult:
        """Parse the complete project first, then generate VHDL with child interfaces available.

        The two-pass flow lets instance port associations use the formal child-port
        direction and width, matching Verilog's implicit connection coercions explicitly
        in VHDL.
        """
        modules: dict[str, ProjectModule] = {}
        prepared, headers, resources, project_diags = self._prepare_project_inputs(paths)

        # Pass 1: parse every source and establish the project interface table.
        parsed: list[tuple[Path, Module, list[Diagnostic]]] = []
        interfaces: dict[str, Module] = {}
        for path, text in prepared:
            try:
                module, diagnostics = self.translator.parser.parse(text, path)
            except (OSError, UnicodeError) as exc:
                project_diags.append(f"ERROR: {path.name}: could not read source: {exc}")
                continue
            if module is None:
                details = "; ".join(d.format() for d in diagnostics) or "parse failed"
                project_diags.append(f"ERROR: {path.name}: {details}")
                continue
            if module.name in interfaces:
                project_diags.append(
                    f"ERROR: duplicate module '{module.name}' in {path.name}."
                )
                continue
            parsed.append((path, module, diagnostics))
            interfaces[module.name] = module

        # Pass 2: generate each module with all loaded child interfaces in scope.
        for path, module, diagnostics in parsed:
            diags = list(diagnostics)
            if any(d.severity == "error" for d in diags):
                result = TranslationResult(module, "", diags)
            else:
                vhdl = self.translator.generator.generate(module, diags, interfaces)
                result = TranslationResult(module, vhdl, diags)
            modules[module.name] = ProjectModule(path=path, result=result)

        result = self._finish_project(modules, project_diags)
        result.headers = headers
        result.resources = resources
        return result

    def _finish_project(self, modules: dict[str, ProjectModule], project_diags: list[str]) -> ProjectResult:
        if not modules:
            return ProjectResult(modules, [], project_diags or ["ERROR: no Verilog modules were loaded."])

        referenced: set[str] = set()
        for pm in modules.values():
            assert pm.module is not None
            for inst in pm.module.instances:
                if inst.module_name in modules:
                    referenced.add(inst.module_name)
                elif inst.module_name in KNOWN_VENDOR_EXTERNALS:
                    # Vendor primitives/macros are supplied by Vivado rather than
                    # by the user's project source set, so they are not unresolved
                    # hierarchy errors.
                    continue
                else:
                    project_diags.append(
                        f"WARNING: {pm.path.name}: instance '{inst.instance_name}' references "
                        f"module '{inst.module_name}', which is not loaded in this project."
                    )

        root_names = [name for name in modules if name not in referenced]
        if not root_names:
            project_diags.append("WARNING: no unique hierarchy root found; possible cyclic instantiation.")
            root_names = list(modules)
        elif len(root_names) > 1:
            project_diags.append(
                "WARNING: multiple hierarchy roots found: " + ", ".join(sorted(root_names))
            )

        roots = [self._build_node(name, None, modules, stack=[]) for name in sorted(root_names)]
        return ProjectResult(modules, roots, project_diags)

    def _build_node(
        self,
        module_name: str,
        instance_name: str | None,
        modules: dict[str, ProjectModule],
        stack: list[str],
    ) -> HierarchyNode:
        if module_name not in modules:
            if module_name in KNOWN_VENDOR_EXTERNALS:
                # Resolved by a Vivado vendor library rather than by a project
                # source file. Keep it in the hierarchy, but do not flag it as
                # an unresolved user module.
                return HierarchyNode(module_name, instance_name, None, True, [])
            return HierarchyNode(module_name, instance_name, None, False, [])

        pm = modules[module_name]
        node = HierarchyNode(module_name, instance_name, pm.path, True, [])
        if module_name in stack:
            node.children.append(HierarchyNode("<recursive hierarchy>", None, None, False, []))
            return node

        next_stack = stack + [module_name]
        assert pm.module is not None
        for inst in pm.module.instances:
            node.children.append(
                self._build_node(inst.module_name, inst.instance_name, modules, next_stack)
            )
        return node

    @staticmethod
    def export_vhdl(project: ProjectResult, destination: Path) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name, pm in sorted(project.modules.items()):
            if not pm.result.ok or not pm.result.vhdl.strip():
                continue
            out = destination / f"{name}.vhdl"
            out.write_text(pm.result.vhdl, encoding="utf-8")
            written.append(out)
        return written
