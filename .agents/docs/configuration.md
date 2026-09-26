# Configuration

Copy `config.example.yaml` to `config.yaml` at the source checkout root. Git ignores the local file. The YAML document must contain a mapping with string keys and a required boolean `example` field. The example contains only `example: true`. The config module owns file resolution and reads only this root file, independent of the current directory.

Import `load_config` from `wash_cam.config` and call it without arguments. It returns an immutable `Config` object with the typed field `example: bool`, accessed as `config.example`. The first successful call reads the file with PyYAML's safe loader. Later calls return the same object from memory. Restart the program to load file changes. Imports do not read files.

The loader raises `wash_cam.config.ConfigError` for file, encoding, parsing, and validation errors. Errors identify the file. Missing-file errors explain how to copy the example. Failed loads are not cached and can be retried. The existing `CAMERA_CROP_BOX` constant remains available for the camera module and does not read from YAML. The dummy command does not read configuration.
