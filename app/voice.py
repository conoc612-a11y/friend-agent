"""음성 받아쓰기. 입력창 옆 마이크로 말하면 글자로 바꿔 대화 입력창에 넣어 준다.

음성 인식은 파이썬 라이브러리 faster-whisper 가 한다. 브라우저 녹음(webm) 디코딩은
faster-whisper 가 함께 배포하는 PyAV(ffmpeg 라이브러리 내장)가 맡아서, 이 기능에
운영체제에 따로 설치할 프로그램은 없다. 대신 인식 모델 파일은 첫 사용 때 내려받아
사용자 폴더의 캐시에 저장되고, 그 뒤로는 네트워크 없이도 돈다.
"""
import io

from app.logs import log

MAX_BYTES = 10 * 1024 * 1024  # 녹음 한 개 최대 10MB
MODEL = "small"  # 한국어 인식 품질과 다운로드 크기(~460MB)·속도의 균형점

_model = None


class VoiceError(Exception):
    pass


def _load():
    """모델을 첫 사용 때 한 번만 내려받고(캐시) 이후 요청은 재사용한다."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log("INFO", "음성 인식 모델을 불러옵니다.", model=MODEL)
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    return _model


def _decode(data: bytes):
    """브라우저 녹음(webm/opus 등)을 모델이 먹는 16kHz mono float32 로 바꾼다.

    shortcut: faster-whisper 1.2.1 의 decode_audio 는 PyAV 19 의 바뀐 open() 서명과
    호환이 안 된다. Python 3.13 Windows 에는 PyAV 19 휠만 있어서 여기서 직접 디코딩한다.
    faster-whisper 가 PyAV 19 를 지원하면 이 함수를 지우고 decode_audio 로 돌아간다.
    """
    import av
    import numpy as np

    container = av.open(io.BytesIO(data))
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks = []
    for packet in container.demux(container.streams.audio[0]):
        for frame in packet.decode():
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray())
    if not chunks:
        raise VoiceError("녹음에 소리가 없습니다.")
    return np.concatenate(chunks, axis=1).flatten().astype("float32")


def transcribe(data: bytes) -> str:
    if not data:
        raise VoiceError("녹음이 비어 있습니다.")
    if len(data) > MAX_BYTES:
        raise VoiceError("녹음이 10MB보다 큽니다.")
    try:
        audio = _decode(data)
    except VoiceError:
        raise
    except Exception as error:
        log("ERROR", "녹음 디코딩 실패", error=type(error).__name__)
        raise VoiceError("녹음 파일을 읽을 수 없습니다.") from error
    try:
        # beam_size=1(탐욕 탐색): 짧은 한마디 기준으로 가장 빠르다. 한국어로 고정해 짧은 녹음의 언어 판별 실패를 막는다.
        segments, _info = _load().transcribe(audio, language="ko", beam_size=1)
        text = "".join(segment.text for segment in segments).strip()
    except Exception as error:
        log("ERROR", "음성 인식 실패", error=type(error).__name__)
        raise VoiceError("말을 글자로 바꾸지 못했습니다.") from error
    log("INFO", "음성 받아쓰기", chars=len(text))
    return text
