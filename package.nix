{
  grim,
  installShellFiles,
  jay,
  lib,
  libnotify,
  python3Packages,
  slurp,
  wl-clipboard,
}:
python3Packages.buildPythonApplication {
  pname = "jay-screenshot";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./pyproject.toml
      ./jay_screenshot.py
    ];
  };

  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.click ];

  nativeBuildInputs = [ installShellFiles ];

  makeWrapperArgs = [
    "--prefix"
    "PATH"
    ":"
    (lib.makeBinPath [
      grim
      jay
      libnotify
      slurp
      wl-clipboard
    ])
  ];

  postFixup = ''
    installShellCompletion --cmd jay-screenshot \
      --bash <(_JAY_SCREENSHOT_COMPLETE=bash_source $out/bin/jay-screenshot) \
      --fish <(_JAY_SCREENSHOT_COMPLETE=fish_source $out/bin/jay-screenshot) \
      --zsh <(_JAY_SCREENSHOT_COMPLETE=zsh_source $out/bin/jay-screenshot)
  '';

  meta = {
    description = "grim screenshot helper for jay";
    homepage = "https://github.com/Ktrompfl/jay-screenshot";
    license = [
      lib.licenses.unlicense
      grim.meta.license
      jay.meta.license
      libnotify.meta.license
      slurp.meta.license
      wl-clipboard.meta.license
    ];
    mainProgram = "jay-screenshot";
    platforms = lib.platforms.linux;
  };
}
