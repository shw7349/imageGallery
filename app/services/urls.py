"""공개 URL 조립은 여기 한 곳에서만 한다(CDN_BASE_URL + derived 키). 원본 키는 절대 URL로 만들지 않는다."""


def cdn_url(cdn_base_url: str, derived_key: str) -> str:
    if not derived_key or derived_key.startswith("originals/"):
        raise ValueError("공개 URL은 파생물 키로만 만든다")
    return f"{cdn_base_url.rstrip('/')}/{derived_key.lstrip('/')}"
