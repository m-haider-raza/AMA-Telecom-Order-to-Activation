"""Generate author-voice film narration locally; keep reference audio outside the repo.

Optional tools, separate from application dependencies: qwen-tts, CUDA PyTorch,
faster-whisper and FFmpeg. Download public models before processing private audio.
No reference audio, embeddings or transcripts are sent to a service.
"""

import argparse
import gc
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
ASR = "Systran/faster-whisper-base.en"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Generate only the first N beats for a local audition")
    parser.add_argument("--story", type=Path, default=ROOT / "app" / "static" / "solution-data.json",
                        help="Storyboard JSON to narrate; defaults to the separate solution film")
    parser.add_argument("--offline-models", action="store_true",
                        help="Use complete local qwen/ and whisper/ model folders without network access")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if not args.reference.is_file():
        parser.error("Reference must be an existing local audio file")
    for path in (args.output_dir, args.model_cache):
        if path.resolve().is_relative_to(ROOT):
            parser.error("Keep private narration and model caches outside the repository")
        path.mkdir(parents=True, exist_ok=True)

    os.environ.update({"HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_XET": "1",
                       "DO_NOT_TRACK": "1", "GRADIO_ANALYTICS_ENABLED": "False"})
    if args.offline_models:
        model_path, asr_path = args.model_cache / "qwen", args.model_cache / "whisper"
        required = [model_path / "config.json", model_path / "model.safetensors",
                    model_path / "speech_tokenizer" / "model.safetensors",
                    asr_path / "model.bin", asr_path / "tokenizer.json"]
        if not all(path.is_file() for path in required):
            raise ValueError("Offline model folders are incomplete; download public model files first.")
        model_path, asr_path = str(model_path), str(asr_path)
        print("Using local-only model folders; network model access is disabled.", flush=True)
    else:
        from huggingface_hub import snapshot_download

        print("Downloading/caching public model weights before loading private audio.", flush=True)
        model_path = snapshot_download(MODEL, local_dir=str(args.model_cache / "qwen"))
        asr_path = snapshot_download(ASR, local_dir=str(args.model_cache / "whisper"),
                                    allow_patterns=["*.json", "*.txt", "*.bin"])
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})

    import numpy as np
    import soundfile as sf
    import torch
    from faster_whisper import WhisperModel
    from qwen_tts import Qwen3TTSModel

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA PyTorch is required for this local narration workflow")
    (args.output_dir / "manifest.json").unlink(missing_ok=True)
    reference = args.output_dir / "reference.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(args.reference),
                    "-ac", "1", "-ar", "24000", str(reference)], check=True)
    print("Transcribing the reference locally on CPU.", flush=True)
    recognizer = WhisperModel(asr_path, device="cpu", compute_type="int8")
    decoded = subprocess.run([
        "ffmpeg", "-loglevel", "error", "-i", str(reference), "-ac", "1", "-ar", "16000",
        "-f", "f32le", "pipe:1",
    ], check=True, capture_output=True)
    segments, _ = recognizer.transcribe(np.frombuffer(decoded.stdout, dtype=np.float32),
                                       language="en", beam_size=5)
    selected = []
    for segment in segments:
        selected.append(segment)
        if segment.end >= 10:
            break
    if not selected or not any(s.text.strip() for s in selected):
        raise ValueError("No reference speech was recognized")
    audio, rate = sf.read(reference, dtype="float32")
    start = max(0, selected[0].start)
    end = min(len(audio) / rate, selected[-1].end)
    reference_audio = audio[round(start * rate):round(end * rate)]
    reference_text = " ".join(s.text.strip() for s in selected)
    (args.output_dir / "reference-transcript.txt").write_text(reference_text, encoding="utf-8")
    del recognizer
    gc.collect()

    torch.manual_seed(30)
    torch.set_num_threads(4)
    print("Loading the local voice model on GPU.", flush=True)
    model = Qwen3TTSModel.from_pretrained(
        model_path, device_map="cuda:0", dtype=torch.bfloat16,
        attn_implementation="sdpa", local_files_only=True,
    )
    prompt = model.create_voice_clone_prompt(
        ref_audio=(reference_audio, rate), ref_text=reference_text,
    )
    story = json.loads(args.story.read_text(encoding="utf-8"))
    texts = []
    for i, scene in enumerate(story["scenes"]):
        for j, beat in enumerate(scene["beats"]):
            if args.limit is not None and len(texts) >= args.limit:
                print("Private audition ready; no full-film manifest was written.", flush=True)
                return
            print(f"Generating {scene['id']} / beat {j + 1} locally.", flush=True)
            wavs, sample_rate = model.generate_voice_clone(
                text=beat["text"], language="English", voice_clone_prompt=prompt,
                max_new_tokens=360, non_streaming_mode=True,
            )
            samples = np.asarray(wavs[0], dtype=np.float32)
            if not np.isfinite(samples).all() or len(samples) < sample_rate * .4:
                raise ValueError(f"Invalid speech output for {scene['id']}, beat {j + 1}")
            output = args.output_dir / f"voice-{i}-{j}.wav"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "f32le", "-ar", str(sample_rate),
                "-ac", "1", "-i", "pipe:0", "-af",
                "silenceremove=start_periods=1:start_duration=0.04:start_threshold=-48dB,"
                "areverse,silenceremove=start_periods=1:start_duration=0.08:start_threshold=-48dB,areverse",
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(output),
            ], input=samples.tobytes(), check=True)
            texts.append(beat["text"])
    manifest = {"kind": "author-voice-synthetic", "model": MODEL, "texts": texts}
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Private local narration complete. Render with --narration-dir.", flush=True)


if __name__ == "__main__":
    main()
