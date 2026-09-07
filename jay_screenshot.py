from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

QUERY_TIMEOUT = 5.0
CAPTURE_TIMEOUT = 30.0
NOTIFY_TIMEOUT = 5.0

REGION_PATTERN = re.compile(r"(-?\d+),(-?\d+) (\d+)x(\d+)")
CANCELLED_PATTERN = re.compile(r"selection (cancelled|failed)", re.IGNORECASE)

DEFAULT_TEMPLATE = "screenshot-%Y%m%d_%Hh%Mm%Ss"

notify_enabled = False

IMAGE_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "ppm": "image/x-portable-pixmap",
}


class ScreenshotError(click.ClickException):
    pass


class DescribedArgument(click.Argument):
    def __init__(
        self, param_decls: Sequence[str], help: str = "", **attrs: Any
    ) -> None:
        self.help = help
        super().__init__(param_decls, **attrs)

    def get_help_record(self, ctx: click.Context) -> tuple[str, str]:
        return self.human_readable_name, self.help


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.x},{self.y} {self.width}x{self.height}"

    @classmethod
    def parse(cls, text: str) -> Region:
        match = REGION_PATTERN.fullmatch(text.strip())
        if match is None:
            raise ValueError(f"{text.strip()!r} is not of the form '<x>,<y> <w>x<h>'")
        region = cls(*(int(group) for group in match.groups()))
        if region.width == 0 or region.height == 0:
            raise ValueError(f"{text.strip()!r} is empty")
        return region


class RegionParamType(click.ParamType):
    name: str = "region"

    def convert(
        self, value: Any, param: click.Parameter | None, ctx: click.Context | None
    ) -> Region:
        if isinstance(value, Region):
            return value
        try:
            return Region.parse(value)
        except ValueError as error:
            self.fail(str(error), param, ctx)


REGION = RegionParamType()


def set_notify(ctx: click.Context, param: click.Parameter, value: bool) -> bool:
    global notify_enabled
    notify_enabled = value
    return value


def notify(
    message: str,
    *,
    urgent: bool = False,
    image: Path | None = None,
) -> None:
    if not notify_enabled or shutil.which("notify-send") is None:
        return
    argv = [
        "notify-send",
        f"--urgency={'critical' if urgent else 'normal'}",
        "screenshot",
        message,
    ]
    if image is not None:
        argv += ["--hint", f"string:image-path:{image}"]
    try:
        subprocess.run(
            argv,
            capture_output=True,
            check=False,
            timeout=NOTIFY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pass


def run(
    argv: list[str],
    *,
    stdin: bytes | None = None,
    timeout: float | None = None,
    detach: bool = False,
) -> bytes:
    if shutil.which(argv[0]) is None:
        raise ScreenshotError(f"{argv[0]} not found on PATH")
    with tempfile.TemporaryFile() as source:
        if stdin is not None:
            source.write(stdin)
            source.seek(0)
        try:
            process = subprocess.run(
                argv,
                stdin=source,
                stdout=subprocess.DEVNULL if detach else subprocess.PIPE,
                stderr=subprocess.DEVNULL if detach else subprocess.PIPE,
                timeout=timeout,
                check=True,
            )
        except subprocess.TimeoutExpired:
            raise ScreenshotError(f"{argv[0]} timed out after {timeout:g}s") from None
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or b"").decode(errors="replace").strip()
            detail = (
                stderr.splitlines()[-1]
                if stderr
                else f"exited with status {error.returncode}"
            )
            raise ScreenshotError(f"{argv[0]}: {detail}") from None
    return process.stdout or b""


@contextmanager
def abort_if_cancelled() -> Generator[None, None, None]:
    try:
        yield
    except ScreenshotError as error:
        if CANCELLED_PATTERN.search(str(error)):
            raise click.Abort() from None
        raise


def query(*args: str, timeout: float | None = QUERY_TIMEOUT) -> dict[str, Any] | None:
    text = run(["jay", "--json", "tree", "query", *args], timeout=timeout).decode(
        errors="replace"
    )
    if not text.strip():
        return None
    try:
        node, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except json.JSONDecodeError:
        raise ScreenshotError("jay returned malformed JSON") from None
    if not isinstance(node, dict):
        raise ScreenshotError("jay returned an unexpected node")
    return node


def selected(kind: str) -> dict[str, Any]:
    with abort_if_cancelled():
        node = query(f"select-{kind}", timeout=None)
    if node is None:
        raise ScreenshotError(f"jay selected no {kind}")
    return node


def region_of(node: dict[str, Any]) -> Region:
    position = node.get("position")
    if not isinstance(position, dict):
        raise ScreenshotError(f"{node.get('type', 'node')} has no position")
    try:
        region = Region(
            *(int(position[key]) for key in ("x1", "y1", "width", "height"))
        )
    except (KeyError, TypeError, ValueError):
        raise ScreenshotError(
            f"{node.get('type', 'node')} has a malformed position"
        ) from None
    if region.width == 0 or region.height == 0:
        raise ScreenshotError(f"{node.get('type', 'node')} is not currently shown")
    return region


def tree_names(node_type: str, field: str) -> list[str]:
    try:
        root = query("-r", "root")
    except ScreenshotError:
        return []
    names: set[str] = set()
    stack = [root] if root is not None else []
    while stack:
        node = stack.pop()
        if node.get("type") == node_type and node.get(field):
            names.add(str(node[field]))
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(child for child in children if isinstance(child, dict))
    return sorted(names)


def complete_workspace(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    return [
        name
        for name in tree_names("workspace", "workspace")
        if name.startswith(incomplete)
    ]


def complete_output(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    return [
        name for name in tree_names("output", "output") if name.startswith(incomplete)
    ]


def expand(template: str) -> Path:
    try:
        return Path(time.strftime(template)).expanduser()
    except ValueError as error:
        raise ScreenshotError(
            f"{template!r} is not a valid file name: {error}"
        ) from None


def write_file(image: bytes, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
    except OSError as error:
        raise ScreenshotError(f"cannot write {path}: {error.strerror}") from None


def preview(image: bytes, image_type: str) -> Path | None:
    directory = os.environ.get("XDG_RUNTIME_DIR")
    if directory is None:
        return None
    path = Path(directory) / f"jay-screenshot-preview.{image_type}"
    try:
        path.write_bytes(image)
    except OSError:
        return None
    return path


@dataclass(frozen=True)
class Capture:
    file: str | None
    copy: bool
    scale: float | None
    image_type: str
    quality: int
    level: int

    def take(self, *source: str) -> None:
        args = ["-t", self.image_type]
        if self.scale is not None:
            args += ["-s", str(self.scale)]
        if self.image_type == "jpeg":
            args += ["-q", str(self.quality)]
        if self.image_type == "png":
            args += ["-l", str(self.level)]
        image = run(["grim", *args, *source, "-"], timeout=CAPTURE_TIMEOUT)
        if not image:
            raise ScreenshotError("grim captured nothing")
        self.deliver(image)

    def deliver(self, image: bytes) -> None:
        path: Path | None = None
        if self.file == "-":
            try:
                sys.stdout.buffer.write(image)
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                raise ScreenshotError(
                    "stdout was closed before the image was written"
                ) from None
        elif self.file is not None:
            path = expand(self.file)
        elif not self.copy:
            directory = os.environ.get("XDG_PICTURES_DIR") or "."
            path = Path(directory) / expand(f"{DEFAULT_TEMPLATE}.{self.image_type}")
        if path is not None:
            write_file(image, path)
        if self.copy:
            run(
                ["wl-copy", "--type", IMAGE_TYPES[self.image_type]],
                stdin=image,
                detach=True,
            )
        self.announce(image, path)

    def announce(self, image: bytes, path: Path | None) -> None:
        if not notify_enabled:
            return
        done = [f"saved to {path}"] if path is not None else []
        if self.copy:
            done.append("copied to the clipboard")
        if not done:
            return
        notify(" and ".join(done), image=path or preview(image, self.image_type))


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "The image is written to a file, copied to the clipboard or piped on; "
        "editing it is left to whatever it is piped into.\n"
        "\n"
        "\b\n"
        "Examples:\n"
        "  jay-screenshot --file - window --active | satty --filename -\n"
        "  jay-screenshot --copy region --select\n"
        "  jay-screenshot --notify workspace 3\n"
        "  jay-screenshot -t jpeg -q 90 -f '~/pictures/%Y-%m-%d.jpg' output DP-2\n"
    ),
)
@click.option(
    "-f",
    "--file",
    metavar="TEMPLATE",
    show_default=(
        f"{DEFAULT_TEMPLATE}.<type> below $XDG_PICTURES_DIR or the working directory"
    ),
    help=(
        "Write the image to TEMPLATE, or to standard output if it is '-'. "
        "TEMPLATE may contain strftime placeholders such as %Y-%m-%d, and "
        "missing directories in it are created."
    ),
)
@click.option(
    "-c",
    "--copy",
    is_flag=True,
    help=("Copy the image to the clipboard."),
)
@click.option(
    "-s",
    "--scale",
    metavar="FACTOR",
    type=click.FloatRange(min=0, min_open=True),
    show_default="the highest scale of all outputs",
    help="Scale the image by FACTOR.",
)
@click.option(
    "-t",
    "--type",
    "image_type",
    type=click.Choice(sorted(IMAGE_TYPES)),
    default="png",
    show_default=True,
    help="Encode the image as TYPE and set the default file extension.",
)
@click.option(
    "-q",
    "--quality",
    metavar="QUALITY",
    type=click.IntRange(0, 100),
    default=80,
    show_default=True,
    help="Compress a jpeg image with QUALITY. Ignored for any other type.",
)
@click.option(
    "-l",
    "--level",
    metavar="LEVEL",
    type=click.IntRange(0, 9),
    default=6,
    show_default=True,
    help=(
        "Compress a png image with LEVEL, from 0 for none to 9 for smallest "
        "but slowest. Ignored for any other type."
    ),
)
@click.option(
    "--notify",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=set_notify,
    help=("Notify the user about successful screenshots or errors."),
)
@click.pass_context
def cli(
    ctx: click.Context,
    file: str | None,
    copy: bool,
    scale: float | None,
    image_type: str,
    quality: int,
    level: int,
) -> None:
    ctx.obj = Capture(
        file=file,
        copy=copy,
        scale=scale,
        image_type=image_type,
        quality=quality,
        level=level,
    )


@cli.command()
@click.option(
    "--active",
    is_flag=True,
    help="Capture the focused window.",
)
@click.option(
    "--select",
    is_flag=True,
    help="Interactively select the window to capture.",
)
@click.pass_obj
def window(capture: Capture, active: bool, select: bool) -> None:
    """Capture a window."""
    if active and select:
        raise click.UsageError("--active and --select are mutually exclusive")
    if not active and not select:
        raise click.UsageError("specify a window with --active or --select")
    if select:
        node = selected("window")
    else:
        node = query("match-windows", "-e", "focused = true")
        if node is None:
            raise ScreenshotError("no window is focused, so there is none to capture")
    capture.take("-g", str(region_of(node)))


@cli.command()
@click.argument(
    "name",
    cls=DescribedArgument,
    required=False,
    shell_complete=complete_workspace,
    help=("The workspace to capture, such as 1."),
)
@click.option(
    "--active",
    is_flag=True,
    help=("Capture the workspace of the focused window."),
)
@click.option(
    "--select",
    is_flag=True,
    help=("Interactively select the workspace to capture."),
)
@click.pass_obj
def workspace(capture: Capture, name: str | None, active: bool, select: bool) -> None:
    """Capture a workspace."""
    sources = [name is not None, active, select]
    if sum(sources) > 1:
        raise click.UsageError("NAME, --active and --select are mutually exclusive")
    if not any(sources):
        raise click.UsageError("specify a wroskpace with NAME, --active or --select")
    if name is not None:
        node = query("workspace-name", name)
        if node is None:
            raise ScreenshotError(f"there is no workspace {name}")
    elif select:
        node = selected("workspace")
    else:
        focused = query("match-windows", "-e", "focused = true")
        if focused is None:
            raise ScreenshotError(
                "no window is focused, so jay cannot tell which workspace is meant"
            )
        focused_name = str(focused.get("workspace") or "")
        if not focused_name:
            raise ScreenshotError("the focused window is not on a workspace")
        node = query("workspace-name", focused_name)
        if node is None:
            raise ScreenshotError(f"there is no workspace {focused_name}")
    capture.take("-g", str(region_of(node)))


@cli.command()
@click.argument(
    "name",
    cls=DescribedArgument,
    required=False,
    shell_complete=complete_output,
    help=("The output to capture, a connector such as DP-2."),
)
@click.option(
    "--active",
    is_flag=True,
    help=("Capture the output the of focused window is on."),
)
@click.pass_obj
def output(capture: Capture, name: str | None, active: bool) -> None:
    """Capture an output."""
    if name is not None and active:
        raise click.UsageError("NAME and --active are mutually exclusive")
    if name is None and not active:
        raise click.UsageError("specify an output with NAME or --active")
    if name is not None:
        connector = name
    else:
        focused = query("match-windows", "-e", "focused = true")
        if focused is None:
            raise ScreenshotError(
                "no window is focused, so jay cannot tell which output is meant"
            )
        workspace_name = str(focused.get("workspace") or "")
        if not workspace_name:
            raise ScreenshotError("the focused window is not on a workspace")
        node = query("workspace-name", workspace_name)
        if node is None:
            raise ScreenshotError(f"there is no workspace {workspace_name}")
        output_name = node.get("output")
        if not output_name:
            raise ScreenshotError("the focused workspace is not on an output")
        connector = str(output_name)
    capture.take("-o", connector)


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument(
    "geometry",
    cls=DescribedArgument,
    type=REGION,
    required=False,
    help=("The region to capture, specified as '<x>,<y> <w>x<h>'."),
)
@click.option(
    "--select",
    is_flag=True,
    help="Interactively select the region to capture.",
)
@click.pass_obj
def region(capture: Capture, geometry: Region | None, select: bool) -> None:
    """Capture a region."""
    if geometry is not None and select:
        raise click.UsageError("GEOMETRY and --select are mutually exclusive")
    if geometry is None and not select:
        raise click.UsageError("specify a region with GEOMETRY or --select")
    if geometry is not None:
        target = geometry
    else:
        with abort_if_cancelled():
            text = run(["slurp"], timeout=None).decode(errors="replace")
        try:
            target = Region.parse(text)
        except ValueError as error:
            raise ScreenshotError(f"slurp: {error}") from None
    capture.take("-g", str(target))


def main() -> None:
    try:
        cli.main(standalone_mode=False)
    except click.ClickException as error:
        notify(error.format_message(), urgent=True)
        error.show()
        sys.exit(error.exit_code)
    except click.Abort:
        sys.exit(1)


if __name__ == "__main__":
    main()
