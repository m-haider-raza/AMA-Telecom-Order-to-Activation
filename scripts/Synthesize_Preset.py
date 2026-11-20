"""Generate a distinct local preset narrator; never reads an author voice sample."""

import argparse
import hashlib
import json
import wave
from pathlib import Path

import numpy as np
import onnxruntime as rt
from kokoro_onnx import Kokoro

MODEL_HASHES = {
    "kokoro-v1.0.onnx": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def synthesize(story_path, models, output, voice="af_heart", speed=1.08):
    root = Path(__file__).resolve().parents[1]
    profiles = {
        "af": ("en-us", "American-English female"),
        "am": ("en-us", "American-English male"),
        "bf": ("en-gb", "British-English female"),
        "bm": ("en-gb", "British-English male"),
    }
    if voice[:2] not in profiles:
        raise ValueError("Choose an English narrator preset (af_, am_, bf_ or bm_)")
    language, description = profiles[voice[:2]]
    if output.resolve().is_relative_to(root):
        raise ValueError("Keep intermediate voice audio outside the repository")
    for name, expected in MODEL_HASHES.items():
        with (models / name).open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise ValueError(f"Local narrator asset differs from the pinned model: {name}")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists() or any(output.glob("voice-*.wav")):
        raise FileExistsError("Existing narration is protected; choose a fresh output directory")
    options = rt.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = rt.InferenceSession(str(models / "kokoro-v1.0.onnx"), sess_options=options,
                                 providers=["CPUExecutionProvider"])
    model = Kokoro.from_session(session, str(models / "voices-v1.0.bin"))
    story = json.loads(story_path.read_text(encoding="utf-8"))
    texts, spoken_texts = [], []
    for i, scene in enumerate(story["scenes"]):
        for j, beat in enumerate(scene["beats"]):
            spoken = beat["text"]
            audio, rate = model.create(spoken, voice=voice, speed=speed, lang=language)
            audio = np.asarray(audio, dtype=np.float32)
            if rate != 24000 or not np.isfinite(audio).all() or len(audio) < rate * .4:
                raise ValueError(f"Invalid narration for {scene['id']} / {j}")
            pcm = np.rint(np.clip(audio, -.999, .999) * 32767).astype("<i2")
            with wave.open(str(output / f"voice-{i}-{j}.wav"), "wb") as target:
                target.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                target.writeframes(pcm.tobytes())
            print(f"{scene['id']} / {j + 1}: {len(audio)/rate:.2f}s", flush=True)
            texts.append(beat["text"])
            spoken_texts.append(spoken)
    manifest = {
        "kind": "local-preset-neural", "model": "Kokoro-82M v1.0", "voice": voice,
        "voiceDescription": f"Distinct {description} preset; not the author's voice",
        "language": language,
        "speed": speed, "texts": texts, "spokenTexts": spoken_texts, "modelSHA256": MODEL_HASHES,
        "source": "https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1",
        "modelLicense": "Apache-2.0", "runtimeLicense": "MIT", "referenceAudioUsed": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Distinct local narrator complete. No reference voice was used.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speed", type=float, default=1.08)
    args = parser.parse_args()
    synthesize(args.story, args.models, args.output, args.voice, args.speed)


if __name__ == "__main__":
    main()
