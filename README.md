# jay-screenshot

[grim](https://sr.ht/~emersion/grim) captures a Wayland output or a region of one, but it does not know about windows, workspaces or what is currently focused.
This helper asks the [jay](https://github.com/mahkoh/jay) compositor for those regions and hands them to grim.

Windows, workspaces, outputs and regions can be named, picked interactively through jay or [slurp](https://github.com/emersion/slurp), or taken from the current keyboard focus.
The image is written to a file, copied to the clipboard with `wl-copy` or piped on, and `notify-send` reports the result when `--notify` is given.
Editing the image is left to whatever it is piped into.

## Installation

Add `jay-screenshot` to your flake inputs in `flake.nix`:

```nix
{
  inputs = {
    jay.url = "github:mahkoh/jay";

    jay-screenshot = {
      url = "github:Ktrompfl/jay-screenshot";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.jay.follows = "jay";
    };
  };
}
```

Then add it to your packages:

```nix
{ inputs, pkgs, ... }: {
  environment.systemPackages = [
    inputs.jay-screenshot.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
```

Alternatively, you can install it with the overlay:

```nix
{ inputs, pkgs, ... }: {
  nixpkgs.overlays = [
    inputs.jay-screenshot.overlays.default
  ];

  environment.systemPackages = [
    pkgs.jay-screenshot
  ];
}
```

## Permissions

To create screenshots, `grim` needs access to the `screencopy` protocols, which is privileged on jay. For the `--copy` option, `wl-copy` needs access to the `data-control` protocols.
You can grant access with client capabilities in your jay config:

```toml
[[clients]]
match.exe-regex = '/\.?grim(-wrapped)?$'
capabilities = ["screencopy"]

[[clients]]
match.exe-regex = '/\.?wl-copy(-wrapped)?$'
capabilities = ["data-control"]
```

Alternatively, you can execute the command with access to all privileged protocols using `jay run-privileged` or the `privileged` option for shortcuts:

```toml
[shortcuts]
logo-s = { type = "exec", exec = { prog = "jay-screenshot", args = ["--notify", "--copy", "--file", "~/pictures/%Y%m%d_%Hh%Mm%Ss.png", "output", "--active"], privileged = true } }
```

## Usage

```console
$ jay-screenshot window --active                 # capture the focused window
$ jay-screenshot window --select                 # select a window to capture
$ jay-screenshot workspace 3                     # capture a workspace by name
$ jay-screenshot output DP-2                     # capture an output
$ jay-screenshot region --select                 # pick a region with slurp
$ jay-screenshot region '0,0 1920x1080'          # capture a region by geometry
```

By default the image is written to `screenshot-%Y%m%d_%Hh%Mm%Ss.png` below `$XDG_PICTURES_DIR`, or the working directory if that is unset.
`--file` overrides the name, `--copy` copies the image to the clipboard and `--file -` writes it to standard output:

```console
$ jay-screenshot --copy region --select
$ jay-screenshot --notify workspace 3
$ jay-screenshot -t jpeg -q 90 -f '~/pictures/%Y-%m-%d.jpg' output DP-2
$ jay-screenshot --file - window --active | satty --filename -
```

You can bind the commands to shortcuts in your jay config:

```toml
[shortcuts]
logo-s = { type = "exec", exec = ["jay-screenshot", "--notify", "--copy", "--file", "~/pictures/%Y%m%d_%Hh%Mm%Ss.png", "output", "--active"] }
logo-shift-s = { type = "exec", exec = ["jay-screenshot", "--notify", "--copy", "--file", "~/pictures/%Y%m%d_%Hh%Mm%Ss.png", "window", "--active"] }
logo-ctrl-s = { type = "exec", exec = ["jay-screenshot", "--notify", "--copy", "--file", "~/pictures/%Y%m%d_%Hh%Mm%Ss.png", "workspace", "--active"] }
logo-shift-ctrl-s = { type = "exec", exec = { shell = "jay-screenshot --type ppm --file - output --active | satty --filename -" } }
```

## Advanced

When jay is configured with a shared library, the focused regions can be accessed directly.
This allows to capture focused outputs or workspaces with no window on them:

```rust
use jay_config::{
    client::{CC_DATA_CONTROL, CC_SCREENCOPY, ClientCriterion},
    config,
    exec::Command,
    input::get_default_seat,
    keyboard::{
        mods::{CTRL, LOGO, SHIFT},
        syms::SYM_s,
    },
};

fn screenshot((x, y): (i32, i32), (width, height): (i32, i32)) {
    if width > 0 && height > 0 {
        Command::new("jay-screenshot")
            .arg("--notify")
            .arg("--copy")
            .arg("--file")
            .arg("~/pictures/%Y%m%d_%Hh%Mm%Ss.png")
            .arg("region")
            .arg(&format!("{x},{y} {width}x{height}"))
            .spawn();
    }
}

fn configure() {
    ClientCriterion::ExeRegex(r"/\.?grim(-wrapped)?$")
        .to_matcher()
        .set_capabilities(CC_SCREENCOPY);
    ClientCriterion::ExeRegex(r"/\.?wl-copy(-wrapped)?$")
        .to_matcher()
        .set_capabilities(CC_DATA_CONTROL);

    let seat = get_default_seat();

    seat.bind(LOGO | SYM_s, move || {
        let output = seat.get_keyboard_connector();
        screenshot(output.position(), output.size());
    });
    seat.bind(LOGO | SHIFT | SYM_s, move || {
        let window = seat.window();
        screenshot(window.position(), window.size());
    });
    seat.bind(LOGO | CTRL | SYM_s, move || {
        let workspace = seat.get_workspace();
        screenshot(workspace.position(), workspace.size());
    });
    seat.bind(LOGO | SHIFT | CTRL | SYM_s, || {
        Command::new("sh")
            .arg("-c")
            .arg("jay-screenshot --type ppm --file - output --active | satty --filename -")
            .spawn();
    });
}

config!(configure);
```

## License

The Unlicense, see [LICENSE](LICENSE).
The tools it invokes carry their own: grim and slurp are MIT, jay is GPL-3.0, wl-clipboard is GPL-3.0-or-later and libnotify is LGPL-2.1.
