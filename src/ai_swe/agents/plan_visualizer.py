"""
Rich CLI visualisation for implementation plans.

Renders a beautiful, colour-coded terminal view of an
:class:`~ai_swe.agents.planner_models.ImplementationPlan` using the
`rich <https://github.com/Textualize/rich>`_ library.

Sections rendered
-----------------
1. **Header panel** — task + summary
2. **Architecture impact**
3. **Step table** — step #, goal, files, risk (colour-coded), dependencies
4. **File change summary** — created / modified / deleted
5. **Risk assessment** — aggregate risk with emoji indicators
6. **Testing strategy**
7. **Risks & mitigations**
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from ai_swe.agents.planner_models import ImplementationPlan, RiskLevel

# ---------------------------------------------------------------------------
# Risk level styling
# ---------------------------------------------------------------------------

_RISK_STYLES: dict[RiskLevel, tuple[str, str]] = {
    RiskLevel.LOW: ("🟢", "green"),
    RiskLevel.MEDIUM: ("🟡", "yellow"),
    RiskLevel.HIGH: ("🟠", "dark_orange"),
    RiskLevel.CRITICAL: ("🔴", "bold red"),
}


def _risk_text(level: RiskLevel) -> Text:
    """Return a styled ``Text`` object for a risk level."""
    emoji, style = _RISK_STYLES.get(level, ("⚪", "white"))
    return Text(f"{emoji} {level.value.upper()}", style=style)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def visualize_plan(
    plan: ImplementationPlan,
    console: Console | None = None,
    *,
    show_file_details: bool = True,
) -> None:
    """
    Print a beautiful, colour-coded plan to the terminal.

    Args:
        plan:              The validated implementation plan.
        console:           Rich ``Console`` to print to (default: new one).
        show_file_details: If True, include the file change summary section.
    """
    if console is None:
        console = Console()

    # ── 1. Header ─────────────────────────────────────────────────────
    header_content = Text()
    header_content.append("Task: ", style="bold cyan")
    header_content.append(plan.task + "\n\n", style="white")
    header_content.append(plan.summary, style="italic")

    console.print()
    console.print(
        Panel(
            header_content,
            title="[bold bright_cyan]📋 Implementation Plan[/]",
            border_style="bright_cyan",
            padding=(1, 2),
        )
    )

    # ── 2. Architecture impact ────────────────────────────────────────
    if plan.architecture_impact:
        console.print()
        console.print(
            Panel(
                Text(plan.architecture_impact, style="white"),
                title="[bold magenta]🏗  Architecture Impact[/]",
                border_style="magenta",
                padding=(0, 2),
            )
        )

    # ── 3. Step-by-step table ─────────────────────────────────────────
    console.print()
    table = Table(
        title="[bold bright_yellow]📝 Implementation Steps[/]",
        show_header=True,
        header_style="bold bright_white on dark_blue",
        border_style="bright_blue",
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", style="bold cyan", width=4, justify="center")
    table.add_column("Goal", style="white", ratio=3)
    table.add_column("Files", style="dim white", ratio=2)
    table.add_column("Risk", width=12, justify="center")
    table.add_column("Deps", style="dim cyan", width=8, justify="center")

    for step in plan.steps:
        files_str = "\n".join(step.files_involved[:5])
        if len(step.files_involved) > 5:
            files_str += f"\n+{len(step.files_involved) - 5} more"

        deps_str = (
            ", ".join(str(d) for d in step.dependencies)
            if step.dependencies
            else "—"
        )

        table.add_row(
            str(step.step_number),
            step.goal,
            files_str,
            _risk_text(step.risk_level),
            deps_str,
        )

    console.print(table)

    # ── 4. Step details (reasoning) ───────────────────────────────────
    console.print()
    detail_tree = Tree(
        "[bold bright_green]🔍 Step Details & Reasoning[/]",
        guide_style="bright_blue",
    )

    for step in plan.steps:
        step_branch = detail_tree.add(
            f"[bold cyan]Step {step.step_number}:[/] {step.goal}"
        )
        step_branch.add(f"[dim]Reasoning:[/] {step.reasoning}")
        step_branch.add(f"[dim]Expected outcome:[/] {step.expected_outcome}")

    console.print(detail_tree)

    # ── 5. File change summary ────────────────────────────────────────
    if show_file_details:
        console.print()
        file_tree = Tree(
            "[bold bright_magenta]📁 File Changes[/]",
            guide_style="magenta",
        )

        if plan.files_to_create:
            create_branch = file_tree.add("[bold green]✨ Create[/]")
            for f in plan.files_to_create:
                create_branch.add(f"[green]{f}[/]")

        if plan.files_to_modify:
            modify_branch = file_tree.add("[bold yellow]✏️  Modify[/]")
            for f in plan.files_to_modify:
                modify_branch.add(f"[yellow]{f}[/]")

        if plan.files_to_delete:
            delete_branch = file_tree.add("[bold red]🗑  Delete[/]")
            for f in plan.files_to_delete:
                delete_branch.add(f"[red]{f}[/]")

        console.print(file_tree)

    # ── 6. Risk assessment ────────────────────────────────────────────
    console.print()
    risk_counts: dict[RiskLevel, int] = {}
    for step in plan.steps:
        risk_counts[step.risk_level] = risk_counts.get(step.risk_level, 0) + 1

    risk_parts: list[Text] = []
    for level in (RiskLevel.CRITICAL, RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW):
        count = risk_counts.get(level, 0)
        if count > 0:
            emoji, style = _RISK_STYLES[level]
            t = Text(f"  {emoji} {level.value.upper()}: {count} step(s)  ", style=style)
            risk_parts.append(t)

    if risk_parts:
        risk_content = Text()
        for part in risk_parts:
            risk_content.append_text(part)
            risk_content.append("\n")

        risk_content.append("\nEstimated complexity: ", style="dim")
        risk_content.append(plan.estimated_complexity.upper(), style="bold")

        console.print(
            Panel(
                risk_content,
                title="[bold dark_orange]⚠️  Risk Assessment[/]",
                border_style="dark_orange",
                padding=(0, 2),
            )
        )

    # ── 7. Testing strategy ───────────────────────────────────────────
    if plan.testing_strategy:
        console.print()
        console.print(
            Panel(
                Text(plan.testing_strategy, style="white"),
                title="[bold green]🧪 Testing Strategy[/]",
                border_style="green",
                padding=(0, 2),
            )
        )

    # ── 8. Risks & mitigations ────────────────────────────────────────
    if plan.risks_and_mitigations:
        console.print()
        risk_list = Tree(
            "[bold red]🛡  Risks & Mitigations[/]",
            guide_style="red",
        )
        for item in plan.risks_and_mitigations:
            risk_list.add(f"[white]{item}[/]")
        console.print(risk_list)

    # ── Footer ────────────────────────────────────────────────────────
    console.print()
    console.print(
        f"[dim]Total steps: {len(plan.steps)} │ "
        f"Files to create: {len(plan.files_to_create)} │ "
        f"Files to modify: {len(plan.files_to_modify)} │ "
        f"Files to delete: {len(plan.files_to_delete)}[/]"
    )
    console.print()
