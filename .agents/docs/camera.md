# Camera

The `wash_cam.camera` package exposes `take_picture(camera_index: int = 0) -> str`. It opens the webcam with OpenCV, captures one frame, saves it as a timestamped JPEG (microsecond precision to avoid same-second filename collisions) under `tmp/images/`, and returns the saved file's name (not the full path). `tmp/` is git-ignored.

It raises `RuntimeError` if the camera at `camera_index` cannot be opened or a frame cannot be read. On macOS, OpenCV requires camera access to be granted to the calling process under System Settings > Privacy & Security > Camera, or `isOpened()` fails.

On the current dev machine, camera index `0` resolves to the Logitech Brio webcam, so it is used as the default. Index mapping is OS/device-dependent and not guaranteed to hold on other machines; callers can pass an explicit `camera_index` to override.
