"""저장소 고르기. STORAGE 환경변수에 따라 메모리 저장소나 Supabase 저장소를 만든다."""

DEFAULT_SETTINGS = {"location": "서울", "timezone": "Asia/Seoul", "push_enabled": False}

# 크레딧 차감 개수 상품표 (원가·기능 변경 시 한 줄만 고친다)
CREDIT_COSTS = {"message": 1, "ocr": 3}

# 가입할 때 한 번 주는 무료 크레딧 (signup:<uid> 참조로 한 번만)
SIGNUP_CREDITS = 30


def make_store(settings):
    if settings.storage == "supabase":
        from app.store.supabase_store import SupabaseStore
        return SupabaseStore(settings.supabase_url, settings.supabase_secret_key)
    from app.store.memory_store import MemoryStore
    return MemoryStore()
