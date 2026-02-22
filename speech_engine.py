import speech_recognition as sr
import asyncio
import logging
import edge_tts
import pygame
import os
import tempfile
import hashlib
from faster_whisper import WhisperModel

# logging.basicConfig(level=logging.INFO)

# Initialize Pygame Mixer Lazily
def _ensure_mixer():
    if not pygame.mixer.get_init():
        try:
            pygame.mixer.init()
        except Exception as e:
            logging.error(f"Failed to initialize Pygame mixer: {e}")

# Voice Configuration
VOICE = "en-GB-RyanNeural" 

# Initialize Faster Whisper (Load once)
# Use 'tiny' or 'base' for speed on CPU. 'small' or 'medium' for accuracy.
MODEL_SIZE = "base.en" 
try:
    logging.info(f"Loading Faster Whisper Model ({MODEL_SIZE})...")
    # CRASH FIX: 'int8' quantization causes Access Violation (0xC0000005) on some Windows CPUs without VNNI.
    # Switching to 'float32' is safer (though slower) or disabling it to use Google API fallback.
    # For Hackathon stability, we default to Google API (whisper_model = None) if this crashes, 
    # but since we can't catch SegFaults, we'll comment it out for safety unless requested.
    
    # UNCOMMENT TO TRY LOCAL WHISPER (May crash on some PCs):
    # whisper_model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    # logging.info("Faster Whisper Loaded.")
    whisper_model = None # Force fallback to Google Speech Recognition
    logging.warning("Faster Whisper disabled for stability. Using Google Speech Recognition.")
except Exception as e:
    logging.error(f"Failed to load Faster Whisper: {e}")
    whisper_model = None

# TTS Cache
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_cache")
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)

_is_speaking = False
SILENT_MODE = False

def set_silent_mode(enabled: bool):
    global SILENT_MODE
    SILENT_MODE = enabled
    if enabled:
        stop_speaking()

def stop_speaking():
    """Stops the current speech playback immediately."""
    global _is_speaking
    _is_speaking = False
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
    except Exception as e:
        logging.error(f"Error stopping speech: {e}")

async def speak(text):
    """Generates speech using Edge-TTS and plays it with Pygame (with Caching)."""
    global _is_speaking
    
    if SILENT_MODE:
        logging.info(f"Silent Mode (Speech Suppressed): {text}")
        return

    if not text:
        return

    # Check for interruption before starting
    _ensure_mixer()
    if not pygame.mixer.get_init():
        return

    try:
        # Generate filename based on text hash
        text_hash = hashlib.md5(text.encode()).hexdigest()
        audio_file = os.path.join(CACHE_DIR, f"{text_hash}.mp3")
        
        # Generate if not cached
        if not os.path.exists(audio_file):
            communicate = edge_tts.Communicate(text, VOICE)
            await communicate.save(audio_file)
        
        # Play
        _is_speaking = True
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        
        # Wait for playback to finish
        while pygame.mixer.music.get_busy() and _is_speaking:
            await asyncio.sleep(0.1)
            
        if not _is_speaking:
            pygame.mixer.music.stop()
            
        pygame.mixer.music.unload()
        _is_speaking = False
            
    except Exception as e:
        logging.error(f"Edge-TTS Error: {e}")
        _is_speaking = False

def speak_sync(text):
    """Synchronous wrapper for speak."""
    asyncio.run(speak(text))

def listen_sync():
    """Listens using SpeechRecognition but transcribes with Faster Whisper."""
    recognizer = sr.Recognizer()
    mic = sr.Microphone()
    
    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            logging.info("Listening...")
            try:
                # Capture audio
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                return None
        
        logging.info("Transcribing with Faster Whisper...")
        
        # Save to temp file for Whisper (it needs a file path or similar)
        # We can also pass raw bytes but file is safer for format
        temp_wav = os.path.join(tempfile.gettempdir(), "temp_command.wav")
        with open(temp_wav, "wb") as f:
            f.write(audio.get_wav_data())
            
        if whisper_model:
            segments, info = whisper_model.transcribe(temp_wav, beam_size=5)
            command = " ".join([segment.text for segment in segments]).strip().lower()
        else:
            # Fallback if model failed to load
            # logging.debug("Faster Whisper not loaded, falling back to Google.")
            command = recognizer.recognize_google(audio).lower()
            
        # Cleanup
        try:
            os.remove(temp_wav)
        except:
            pass
            
        logging.info(f"User said: {command}")
        return command
            
    except (sr.UnknownValueError, sr.WaitTimeoutError):
        # Benign errors (silence or timeout)
        return None
        
    except Exception as e:
        import traceback
        logging.error(f"Speech Error: {repr(e)}\n{traceback.format_exc()}")
        return None

async def listen():
    return await asyncio.to_thread(listen_sync)
