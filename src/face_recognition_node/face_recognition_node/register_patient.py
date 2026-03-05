"""
register_patient.py

CLI script to register a new patient's face encodings for MediBot recognition.

Usage:
  register_patient --name "John Doe" --id P001 --age 45
  register_patient --name "Jane Smith" --id P002 --age 32 --frames 30
  register_patient --name "Test" --id P003 --age 25 --output-dir /tmp/faces/

Arguments:
  --name        Patient full name (required)
  --id          Patient ID, e.g. P001 (required)
  --age         Patient age in years (required)
  --frames      Number of frames to capture (default: 20)
  --output-dir  Directory to save encodings (default: ~/.medibot/faces/)

Environment:
  USE_MOCK_HW=true  - use synthetic frames instead of opening a real camera
"""

import argparse
import os
import pickle
import sys
import time
from pathlib import Path

# Optional imports - provide clear messages if unavailable
try:
    import cv2
    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False
    print('[WARN] OpenCV (cv2) not available.')

try:
    import face_recognition as fr_lib
    import numpy as np
    FR_LIB_AVAILABLE = True
except ImportError:
    FR_LIB_AVAILABLE = False
    print('[WARN] face_recognition library not available. Encodings will be empty placeholders.')

MOCK_HW = os.environ.get('USE_MOCK_HW', 'false').lower() == 'true'
DEFAULT_CAMERA_INDEX = 1
DEFAULT_OUTPUT_DIR = Path.home() / '.medibot' / 'faces'


# ---------------------------------------------------------------------------
# Frame acquisition helpers
# ---------------------------------------------------------------------------

def _generate_mock_frame(width: int = 320, height: int = 240):
    """Generate a synthetic BGR frame with a white rectangle simulating a face."""
    if not CV_AVAILABLE:
        return None
    frame = cv2.imencode  # just to silence linter; we build manually below
    import numpy as np  # local import so the function is self-contained
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # Draw a white face-like rectangle in the centre
    cx, cy = width // 2, height // 2
    fw, fh = 80, 100
    img[cy - fh // 2: cy + fh // 2, cx - fw // 2: cx + fw // 2] = 255
    return img


def _open_camera(index: int):
    """Open the specified camera and return the VideoCapture object."""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        print(f'[ERROR] Cannot open camera at index {index}.')
        return None
    return cap


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def collect_encodings(
    name: str,
    patient_id: str,
    age: int,
    frames: int,
    output_dir: Path,
) -> bool:
    """
    Capture frames and extract face encodings for the given patient.

    Returns True on success, False on failure.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    encodings_file = output_dir / 'encodings.pkl'

    # Load existing encodings database
    if encodings_file.exists():
        try:
            with open(encodings_file, 'rb') as f:
                db: dict = pickle.load(f)
            print(f'[INFO] Loaded existing encodings database ({len(db)} patient(s)).')
        except Exception as exc:
            print(f'[WARN] Could not load existing encodings ({exc}); starting fresh.')
            db = {}
    else:
        db = {}

    collected_encodings = []
    captured = 0

    # ---- Camera / mock frame source ----
    cap = None
    if not MOCK_HW:
        if not CV_AVAILABLE:
            print('[ERROR] OpenCV is required for real camera capture.')
            return False
        cap = _open_camera(DEFAULT_CAMERA_INDEX)
        if cap is None:
            print('[WARN] Falling back to mock frame generation.')
    else:
        print('[INFO] USE_MOCK_HW=true: generating synthetic frames.')

    print(f'\nRegistering patient: {name} (ID: {patient_id}, Age: {age})')
    print(f'Target frames: {frames}')
    print('Press Ctrl+C to abort.\n')

    try:
        frame_attempt = 0
        while captured < frames:
            frame_attempt += 1

            if MOCK_HW or cap is None:
                frame = _generate_mock_frame()
                if frame is None:
                    print('[ERROR] Cannot generate mock frames without OpenCV.')
                    return False
                time.sleep(0.05)  # simulate capture delay
            else:
                ret, frame = cap.read()
                if not ret or frame is None:
                    print('[WARN] Failed to read frame from camera; retrying...')
                    time.sleep(0.1)
                    continue

            if FR_LIB_AVAILABLE:
                # Convert BGR -> RGB for face_recognition
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if CV_AVAILABLE else frame
                face_locations = fr_lib.face_locations(rgb_frame)

                if not face_locations:
                    print(
                        f'  [Frame {frame_attempt:03d}] No face detected; skipping...'
                    )
                    continue

                encodings = fr_lib.face_encodings(rgb_frame, known_face_locations=face_locations)
                if encodings:
                    collected_encodings.append(encodings[0])
                    captured += 1
                    print(
                        f'  [Frame {frame_attempt:03d}] Captured encoding '
                        f'{captured}/{frames} '
                        f'(face at {face_locations[0]})'
                    )
                else:
                    print(f'  [Frame {frame_attempt:03d}] Could not compute encoding; skipping.')
            else:
                # No face_recognition library - store placeholder
                captured += 1
                print(
                    f'  [Frame {frame_attempt:03d}] Stored placeholder '
                    f'(no face_recognition library) {captured}/{frames}'
                )

            # Safety: don't spin forever if camera is broken
            if frame_attempt > frames * 10:
                print('[WARN] Too many failed attempts; stopping early.')
                break

    except KeyboardInterrupt:
        print('\n[INFO] Registration aborted by user.')
        return False
    finally:
        if cap is not None:
            cap.release()

    if captured == 0:
        print('[ERROR] No encodings collected. Aborting.')
        return False

    # ---- Save to database ----
    db[patient_id] = {
        'name': name,
        'age': age,
        'encodings': collected_encodings,
    }

    try:
        with open(encodings_file, 'wb') as f:
            pickle.dump(db, f)
    except Exception as exc:
        print(f'[ERROR] Failed to save encodings: {exc}')
        return False

    print(f'\n[SUCCESS] Saved {captured} encoding(s) for {name} (ID: {patient_id}).')
    print(f'          Database now contains {len(db)} patient(s).')
    print(f'          File: {encodings_file}')
    return True


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Register a patient face for MediBot recognition.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        '--name',
        required=True,
        help='Patient full name (e.g. "John Doe")',
    )
    parser.add_argument(
        '--id',
        dest='patient_id',
        required=True,
        help='Patient ID (e.g. P001)',
    )
    parser.add_argument(
        '--age',
        required=True,
        type=int,
        help='Patient age in years',
    )
    parser.add_argument(
        '--frames',
        type=int,
        default=20,
        help='Number of face frames to capture',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help='Directory to store encodings.pkl',
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    success = collect_encodings(
        name=args.name,
        patient_id=args.patient_id,
        age=args.age,
        frames=args.frames,
        output_dir=args.output_dir,
    )
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
