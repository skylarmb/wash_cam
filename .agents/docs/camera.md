# Camera

The `wash_cam.camera` package exposes `take_picture(camera_index: int = 0) -> str`. It opens the webcam with OpenCV, captures one frame, crops it to `wash_cam.config.CAMERA_CROP_BOX`, saves the crop as a timestamped JPEG (microsecond precision to avoid same-second filename collisions) under `tmp/images/`, and returns the saved file's name (not the full path). `tmp/` is git-ignored.

`CAMERA_CROP_BOX` lives in `wash_cam/config.py` as a hardcoded `(left, top, right, bottom)` pixel box isolating the wash bays, expressed in reference-frame coordinates at 1920x1080. Rather than re-tuning it when the camera is re-seated, run `wash-cam calibrate`; see `.agents/docs/calibration.md`. `take_picture()` aligns each frame to the reference before cropping, and `crop_to_box()` raises `ValueError` for a box that does not fit the frame.

`tests/test_camera.py` covers `take_picture()` and `run()` without real hardware by monkeypatching `cv2.VideoCapture` with a fake exposing `isOpened()`/`read()`/`release()`, and monkeypatching `capture.IMAGES_DIR` to pytest's `tmp_path`. A small numpy frame stands in for a real camera frame so the crop slicing itself is exercised, not just mocked away.

It raises `RuntimeError` if the camera at `camera_index` cannot be opened or a frame cannot be read. On macOS, OpenCV requires camera access to be granted to the calling process under System Settings > Privacy & Security > Camera, or `isOpened()` fails.

On the current dev machine, camera index `0` resolves to the Logitech Brio webcam, so it is used as the default. Index mapping is OS/device-dependent and not guaranteed to hold on other machines; callers can pass an explicit `camera_index` to override.
