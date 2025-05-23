import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import os
import base64
import sys

# 如果测试从 tests/ 目录运行，则将项目根目录添加到 sys.path
# 这确保 'main' 可以被导入
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app, ModelProcessor

TEST_SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "test_samples")
SAMPLE_VIDEO_PATH = os.path.join(TEST_SAMPLES_DIR, "sample_video.mp4")
INVALID_FILE_PATH = os.path.join(TEST_SAMPLES_DIR, "invalid_file.txt")

@pytest.fixture(scope="session", autouse=True)
def manage_test_files():
    os.makedirs(TEST_SAMPLES_DIR, exist_ok=True)

    # 检查 sample_video.mp4 是否已由前一步的 bash 命令创建
    # 如果没有，此 fixture 不会按照指示尝试在此处重新创建它。
    # ffmpeg 命令应该已经运行过了。

    with open(INVALID_FILE_PATH, "w") as f:
        f.write("This is not a video.")
    
    yield 

    # 测试会话结束后清理示例文件。
    # sample_video.mp4 由 run_in_bash_session 调用管理，因此仅移除 invalid_file.txt
    if os.path.exists(INVALID_FILE_PATH):
        os.remove(INVALID_FILE_PATH)
    
    # 仅当 TEST_SAMPLES_DIR 为空且不是由 ffmpeg 直接创建时，才尝试移除它
    # 为简单起见，并考虑到前一步已创建视频，我们将保留该目录
    # 如果它包含视频。如果在移除 invalid_file.txt 后为空，则尝试移除。
    if os.path.exists(TEST_SAMPLES_DIR) and not os.listdir(TEST_SAMPLES_DIR):
        try:
            os.rmdir(TEST_SAMPLES_DIR)
        except OSError:
            pass


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

@pytest.mark.asyncio
async def test_summarize_video_with_gemini_success():
    mock_gemini_response_obj = MagicMock()
    mock_gemini_response_obj.text = "This is a mocked summary."
    
    # 模拟内部函数 run_gemini_summarization_api 的返回值
    # 该函数由 run_in_thread 调用
    async def mock_run_in_thread_function(func, *args, **kwargs):
        # 直接返回期望的模拟响应，模拟其行为 
        # 当在 run_in_thread 中调用 run_gemini_summarization_api 时的行为。
        return mock_gemini_response_obj.text 

    with patch('main.run_in_thread', new=mock_run_in_thread_function):
        dummy_frames = ["base64image1", "base64image2"]
        prompt = "Summarize these frames."
        # 实际的 ModelProcessor.summarize_video_with_gemini 返回一个字典
        result_dict = await ModelProcessor.summarize_video_with_gemini(dummy_frames, prompt)
        assert result_dict["status"] == "success"
        assert result_dict["summary"] == "This is a mocked summary."
        assert result_dict["model"] == "gemini"

@pytest.mark.asyncio
async def test_summarize_video_with_gemini_api_error():
    async def mock_run_in_thread_raises_exception(func, *args, **kwargs):
        raise Exception("Gemini API Error")

    with patch('main.run_in_thread', new=mock_run_in_thread_raises_exception):
        dummy_frames = ["base64image1"]
        prompt = "Summarize."
        result_dict = await ModelProcessor.summarize_video_with_gemini(dummy_frames, prompt)
        assert result_dict["status"] == "error"
        assert "Gemini API error during summarization: Gemini API Error" in result_dict["message"]
        assert result_dict["model"] == "gemini"


def test_summarize_api_success(client):
    if not os.path.exists(SAMPLE_VIDEO_PATH) or os.path.getsize(SAMPLE_VIDEO_PATH) < 100: 
        pytest.skip("Valid sample video not available or too small. FFMPEG step might have failed or produced an invalid file.")

    # 模拟 ModelProcessor.summarize_video_with_gemini 方法
    mock_summary_response = {
        "status": "success",
        "summary": "Mocked video summary from API test.",
        "model": "gemini", # 确保这与 ModelProcessor 会返回的内容相匹配
        "timestamp": 12345
    }
    with patch('main.ModelProcessor.summarize_video_with_gemini', return_value=mock_summary_response) as mock_summarize:
        response = client.post("/api/video/summarize", json={"video_path": SAMPLE_VIDEO_PATH})
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["summary"] == "Mocked video summary from API test."
        assert data["model_used"] == "gemini" 
        assert "frames_processed" in data
        # 示例视频时长3秒，默认间隔5秒。
        # 帧提取逻辑：0秒。因此预期1帧。
        assert data["frames_processed"] >= 1, f"Expected at least 1 frame, got {data['frames_processed']}"
        mock_summarize.assert_called_once()


def test_summarize_api_video_not_found(client):
    response = client.post("/api/video/summarize", json={"video_path": "non_existent_video.mp4"})
    assert response.status_code == 404 
    data = response.json()
    assert data["status"] == "error"
    assert "Video file not found" in data["message"]


def test_summarize_api_invalid_video_file(client):
    response = client.post("/api/video/summarize", json={"video_path": INVALID_FILE_PATH})
    # 预期500错误，因为 cv2.VideoCapture 可能会失败，并且可能不会作为特定的客户端错误被捕获。
    # 或者如果首先触发了对视频属性（如时长为0）的特定检查，则为400错误。
    assert response.status_code in [400, 500] 
    data = response.json()
    assert data["status"] == "error"
    # 检查可能来自cv2错误或时长检查的消息
    possible_messages = ["Could not open video file", "Video duration is zero or FPS is invalid"]
    assert any(msg in data["message"] for msg in possible_messages)


def test_summarize_api_gemini_call_error(client):
    if not os.path.exists(SAMPLE_VIDEO_PATH) or os.path.getsize(SAMPLE_VIDEO_PATH) < 100:
        pytest.skip("Valid sample video not available or too small. FFMPEG step might have failed.")

    # 模拟 ModelProcessor.summarize_video_with_gemini 以返回一个错误结构
    mock_error_response = {
        "status": "error",
        "message": "Simulated Gemini Error from ModelProcessor",
        "model": "gemini"
    }
    with patch('main.ModelProcessor.summarize_video_with_gemini', return_value=mock_error_response) as mock_summarize_error:
        response = client.post("/api/video/summarize", json={"video_path": SAMPLE_VIDEO_PATH})
        # 端点本身应该仍然能够成功调用（模拟的）ModelProcessor
        # 这个错误来自于摘要的*逻辑*，而不是此情况下的未处理API异常
        assert response.status_code == 500 # 如果摘要失败，端点应返回500
        data = response.json()
        assert data["status"] == "error"
        assert data["message"] == "Simulated Gemini Error from ModelProcessor"
        mock_summarize_error.assert_called_once()

def test_summarize_api_missing_video_path(client):
    response = client.post("/api/video/summarize", json={}) # 缺少 video_path
    assert response.status_code == 422 # FastAPI 针对缺失字段返回的不可处理实体错误
    data = response.json()
    assert "detail" in data
    assert any(d["msg"] == "Field required" and d["loc"] == ["body", "video_path"] for d in data["detail"])

def test_summarize_api_empty_video_path(client):
    response = client.post("/api/video/summarize", json={"video_path": ""})
    # 这很可能会被 os.path.exists 检查捕获。
    assert response.status_code == 404
    data = response.json()
    assert data["status"] == "error"
    assert "Video file not found" in data["message"]

# 帧提取参数的示例测试
def test_summarize_api_custom_extraction_params(client):
    if not os.path.exists(SAMPLE_VIDEO_PATH) or os.path.getsize(SAMPLE_VIDEO_PATH) < 100:
        pytest.skip("Valid sample video not available. FFMPEG step might have failed.")

    mock_summary_response = {
        "status": "success",
        "summary": "Custom params summary.",
        "model": "gemini",
        "timestamp": 12345
    }
    with patch('main.ModelProcessor.summarize_video_with_gemini', return_value=mock_summary_response) as mock_summarize:
        # 示例视频3秒。间隔1秒应产生3帧（0秒, 1秒, 2秒）。最大帧数2。
        # 帧索引: [0, 10, 20] (对于10fps的视频)
        # 由于 max_frames=2 而选择: [0, 20] -> 2帧
        response = client.post("/api/video/summarize", json={
            "video_path": SAMPLE_VIDEO_PATH,
            "extraction_interval_seconds": 1,
            "max_frames": 2
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["summary"] == "Custom params summary."
        assert data["frames_processed"] == 2 # 由于 max_frames 的设置，预期2帧
        mock_summarize.assert_called_once()
