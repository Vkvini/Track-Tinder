1. requirements.txt
(This tells Python what packages to install)

fastapi
uvicorn
python-multipart
librosa
numpy
scipy
jinja2
soundfile
2. main.py
(The main server file)

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from api.routes import router

app = FastAPI(title="DJ Matcher", description="Music Matching Software for DJs")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(router, prefix="/api")

@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
3. core/audio_processor.py
(The math that analyzes the MP3 file)

import librosa
import numpy as np

CAMELOT_MAJOR = {0: "8B", 1: "3B", 2: "10B", 3: "5B", 4: "12B", 5: "7B", 6: "2B", 7: "9B", 8: "4B", 9: "11B", 10: "6B", 11: "1B"}
CAMELOT_MINOR = {0: "5A", 1: "12A", 2: "7A", 3: "2A", 4: "9A", 5: "4A", 6: "11A", 7: "6A", 8: "1A", 9: "8A", 10: "3A", 11: "10A"}

MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

def analyze_audio(file_path: str):
    try:
        y, sr = librosa.load(file_path, sr=None)
        bpm, _ = librosa.beat.beat_track(y=y, sr=sr)
        if isinstance(bpm, np.ndarray): bpm = bpm[0]
        bpm = round(float(bpm), 2)
        y_harmonic, _ = librosa.effects.hpss(y)
        chromagram = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr)
        chroma_vals = np.sum(chromagram, axis=1)
        major_correlations = []
        minor_correlations = []
        for i in range(12):
            major_correlations.append(np.corrcoef(chroma_vals, np.roll(MAJOR_PROFILE, i))[0, 1])
            minor_correlations.append(np.corrcoef(chroma_vals, np.roll(MINOR_PROFILE, i))[0, 1])
        best_major_idx = np.argmax(major_correlations)
        best_minor_idx = np.argmax(minor_correlations)
        if major_correlations[best_major_idx] > minor_correlations[best_minor_idx]:
            key = CAMELOT_MAJOR[best_major_idx]
            mode = "Major"
        else:
            key = CAMELOT_MINOR[best_minor_idx]
            mode = "Minor"
        return {"bpm": bpm, "camelot": key, "mode": mode, "success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
4. core/matcher.py
(The logic that checks harmonic matches)

def get_camelot_matches(camelot_key: str):
    if not camelot_key or len(camelot_key) < 2: return {"perfect": [], "good": []}
    num = int(camelot_key[:-1])
    letter = camelot_key[-1]
    perfect = [camelot_key]
    good = []
    good.append(f"{num}{'B' if letter == 'A' else 'A'}")
    up = num + 1 if num < 12 else 1
    down = num - 1 if num > 1 else 12
    good.append(f"{up}{letter}")
    good.append(f"{down}{letter}")
    return {"perfect": perfect, "good": good}

def get_bpm_match_score(target_bpm: float, candidate_bpm: float) -> float:
    if target_bpm <= 0 or candidate_bpm <= 0: return 0.0
    diff_percent = abs(target_bpm - candidate_bpm) / target_bpm
    if diff_percent > 0.10: return 0.0
    score = 1.0 - (diff_percent * 10)
    return max(0.0, score)

def rank_tracks(target_bpm: float, target_key: str, candidate_tracks: list):
    matches = get_camelot_matches(target_key)
    ranked_results = []
    for track in candidate_tracks:
        track_bpm = track.get("bpm", 0)
        track_key = track.get("camelot", "")
        bpm_score = get_bpm_match_score(target_bpm, track_bpm)
        if track_key in matches["perfect"]: key_score = 1.0; match_type = "Perfect"
        elif track_key in matches["good"]: key_score = 0.8; match_type = "Good"
        else: key_score = 0.0; match_type = "Poor"
        total_score = (key_score * 0.7) + (bpm_score * 0.3)
        if total_score > 0.3:
            result = track.copy()
            result["match_type"] = match_type
            result["score"] = round(total_score * 100)
            ranked_results.append(result)
    ranked_results.sort(key=lambda x: x["score"], reverse=True)
    return ranked_results
5. api/routes.py
(The API that connects the front to the back)

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
import tempfile
import os
import random
from core.audio_processor import analyze_audio
from core.matcher import rank_tracks

router = APIRouter()

MOCK_LIBRARY = [
    {"id": 1, "title": "Strobe", "bpm": 128, "camelot": "8A", "artist": "Deadmau5", "url": "https://youtube.com/mock1"},
    {"id": 2, "title": "Opus", "bpm": 126, "camelot": "8B", "artist": "Eric Prydz", "url": "https://youtube.com/mock2"},
    {"id": 3, "title": "Losing It", "bpm": 125, "camelot": "7A", "artist": "FISHER", "url": "https://youtube.com/mock3"},
]

@router.post("/analyze/file")
async def analyze_uploaded_file(file: UploadFile = File(...)):
    if not file.filename.endswith((".mp3", ".wav", ".m4a")):
        raise HTTPException(status_code=400, detail="Invalid file type.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
        contents = await file.read()
        tmp.write(contents)
        tmp_path = tmp.name
    try:
        result = analyze_audio(tmp_path)
        if not result["success"]: raise HTTPException(status_code=500, detail=result.get("error"))
        recommendations = rank_tracks(result["bpm"], result["camelot"], MOCK_LIBRARY)
        return {"track": {"title": file.filename, "bpm": result["bpm"], "camelot": result["camelot"], "mode": result["mode"]}, "recommendations": recommendations}
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

@router.post("/analyze/search")
async def analyze_by_search(query: str = Form(...)):
    mock_bpm = random.randint(110, 130)
    mock_key = random.choice(["1A", "2A", "3B", "4A", "5B", "6A", "7A", "8A", "9B", "10A", "11B", "12A"])
    recommendations = rank_tracks(mock_bpm, mock_key, MOCK_LIBRARY)
    return {"track": {"title": query, "bpm": mock_bpm, "camelot": mock_key, "mode": "Mocked", "source": "Spotify API (Mock)"}, "recommendations": recommendations}
6. templates/index.html
(The visual page)

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DJ Matcher - Harmonic Mixing</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        .drag-over { border-color: #ec4899 !important; background-color: rgba(236, 72, 153, 0.1) !important; }
        .loader { border-top-color: #ec4899; animation: spinner 1.5s linear infinite; }
        @keyframes spinner { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen font-sans">
    <div class="container mx-auto p-4 max-w-5xl">
        <header class="mb-10 mt-6 text-center">
            <h1 class="text-5xl font-extrabold bg-clip-text text-transparent bg-gradient-to-r from-purple-400 to-pink-600 mb-2">DJ Matcher</h1>
            <p class="text-gray-400 text-lg">Drop an MP3 or Search a Track to find the perfect next song.</p>
        </header>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mb-10">
            <div class="bg-gray-800 p-6 rounded-xl shadow-lg border border-gray-700">
                <h2 class="text-xl font-semibold mb-4 text-purple-400"><i class="fas fa-file-audio mr-2"></i>Analyze Audio File</h2>
                <div id="drop-zone" class="border-2 border-dashed border-gray-600 rounded-lg p-10 text-center cursor-pointer hover:border-pink-500 transition-colors bg-gray-900">
                    <i class="fas fa-cloud-upload-alt text-4xl text-gray-500 mb-3"></i>
                    <p class="text-gray-300">Drag & Drop your MP3 here</p>
                    <input type="file" id="file-input" class="hidden" accept="audio/mpeg, audio/wav, audio/mp4">
                </div>
            </div>

            <div class="bg-gray-800 p-6 rounded-xl shadow-lg border border-gray-700">
                <h2 class="text-xl font-semibold mb-4 text-pink-400"><i class="fas fa-search mr-2"></i>Search Track Name</h2>
                <form id="search-form" class="space-y-4">
                    <div>
                        <input type="text" id="search-input" placeholder="e.g. Strobe Deadmau5" class="w-full bg-gray-900 border border-gray-600 rounded-lg py-3 px-4 text-white focus:outline-none focus:border-pink-500">
                    </div>
                    <button type="button" id="search-btn" class="w-full bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 text-white font-bold py-3 px-4 rounded-lg shadow-lg">
                        Find Matches
                    </button>
                </form>
            </div>
        </div>

        <div id="loading" class="hidden text-center py-10">
            <div class="inline-block w-12 h-12 border-4 border-gray-700 rounded-full loader mb-4"></div>
            <p class="text-pink-400 font-semibold" id="loading-text">Analyzing...</p>
        </div>

        <div id="results" class="hidden space-y-8">
            <div class="bg-gradient-to-r from-gray-800 to-gray-900 p-6 rounded-xl shadow-lg border border-gray-700">
                <h3 class="text-sm uppercase tracking-widest text-gray-400 font-semibold mb-2">Currently Playing</h3>
                <div class="flex justify-between items-center">
                    <h2 id="current-title" class="text-3xl font-bold text-white truncate w-2/3">Track Title</h2>
                    <div class="flex space-x-4">
                        <div class="text-center bg-gray-800 px-4 py-2 rounded-lg border border-gray-600">
                            <span class="block text-xs text-gray-400 uppercase">BPM</span>
                            <span id="current-bpm" class="text-xl font-bold text-pink-400">120</span>
                        </div>
                        <div class="text-center bg-gray-800 px-4 py-2 rounded-lg border border-gray-600">
                            <span class="block text-xs text-gray-400 uppercase">Key</span>
                            <span id="current-key" class="text-xl font-bold text-purple-400">8A</span>
                        </div>
                    </div>
                </div>
            </div>

            <div>
                <h3 class="text-2xl font-bold mb-4"><i class="fas fa-list-ol mr-3 text-pink-500"></i>Recommended Next Tracks</h3>
                <div id="recommendations-list" class="space-y-3"></div>
            </div>
        </div>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
7. static/app.js
(The frontend logic connecting the buttons)

document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const searchBtn = document.getElementById('search-btn');
    const searchInput = document.getElementById('search-input');
    const loading = document.getElementById('loading');
    const results = document.getElementById('results');
    const recommendationsList = document.getElementById('recommendations-list');

    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault(); dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', (e) => { if (e.target.files.length) handleFileUpload(e.target.files[0]); });

    searchBtn.addEventListener('click', (e) => {
        e.preventDefault();
        if (searchInput.value.trim()) handleSearch(searchInput.value.trim());
    });

    async function handleFileUpload(file) {
        showLoading('Analyzing audio (this may take a few seconds)...');
        const formData = new FormData(); formData.append('file', file);
        try {
            const response = await fetch('/api/analyze/file', { method: 'POST', body: formData });
            displayResults(await response.json());
        } catch (error) { alert('Error: ' + error.message); hideLoading(); }
    }

    async function handleSearch(query) {
        showLoading('Searching tracks and calculating matches...');
        const formData = new FormData(); formData.append('query', query);
        try {
            const response = await fetch('/api/analyze/search', { method: 'POST', body: formData });
            displayResults(await response.json());
        } catch (error) { alert('Error: ' + error.message); hideLoading(); }
    }

    function showLoading(text) {
        document.getElementById('loading-text').innerText = text;
        results.classList.add('hidden'); loading.classList.remove('hidden');
    }

    function hideLoading() { loading.classList.add('hidden'); }

    function displayResults(data) {
        hideLoading(); results.classList.remove('hidden');
        document.getElementById('current-title').innerText = data.track.title;
        document.getElementById('current-bpm').innerText = data.track.bpm;
        document.getElementById('current-key').innerText = data.track.camelot;

        recommendationsList.innerHTML = '';
        data.recommendations.forEach((track, index) => {
            let badgeColor = track.match_type === 'Perfect' ? 'bg-green-600' : 'bg-blue-600';
            const row = document.createElement('div');
            row.className = 'bg-gray-800 p-4 rounded-lg flex items-center justify-between border border-gray-700';
            row.innerHTML = `
                <div class="flex items-center w-1/2">
                    <span class="text-gray-500 font-mono w-6 mr-3">${index + 1}.</span>
                    <div><h4 class="font-bold text-white text-lg">${track.title}</h4><p class="text-sm text-gray-400">${track.artist}</p></div>
                </div>
                <div class="flex items-center justify-center space-x-6 w-1/3">
                    <div class="text-center"><span class="text-xs text-gray-500 block uppercase">BPM</span><span class="font-semibold text-gray-200">${track.bpm}</span></div>
                    <div class="text-center"><span class="text-xs text-gray-500 block uppercase">Key</span><span class="font-semibold text-gray-200">${track.camelot}</span></div>
                    <div class="text-center"><span class="text-xs text-gray-500 block uppercase">Match</span><span class="px-2 py-1 text-xs font-bold rounded-full ${badgeColor} text-white">${track.match_type}</span></div>
                </div>
                <div class="w-1/6 text-right"><span class="text-xs text-gray-500 block uppercase">Score</span><span class="font-bold text-green-400">${track.score}%</span></div>
            `;
            recommendationsList.appendChild(row);
        });
    }
});
That's everything! Just make sure your folders are arranged exactly like this on GitHub:

dj-matcher/
├── requirements.txt
├── main.py
├── api/
│   └── routes.py
├── core/
│   ├── audio_processor.py
│   └── matcher.py
├── static/
│   └── app.js
└── templates/
    └── index.html
