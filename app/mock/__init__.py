"""프론트 개발용 목업(MOCK_API=true). 실제 구현이 들어오면 해당 엔드포인트의 목업은 지운다.

목업 데이터는 계약 스키마(app/schemas/*) 객체로 만들어지므로 FastAPI 응답 검증을 그대로 통과한다.
이미지 URL은 scripts/seed_mock_images.py가 MinIO에 올린 실제 이미지를 가리킨다.
"""
