import speech_recognition as sr
import asyncio
import logging
import edge_tts
import pygame
import os
import tempfile

# logging.basicConfig(level=logging.INFO)

# Initialize Pygame Mixer
try:
    pygame.mixer.init()
except Exception as e:
    logging.error(f"Failed to initialize Pygame mixer: {e}")

# Voice Configuration
VOICE = "en-GB-RyanNeural"  # Jarvis-like British Voice
# Other options: en-US-ChristopherNeural, en-US-AriaNeural

def stop_speaking():
    """Stops the current speech playback immediately."""
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception as e:
        logging.error(f"Error stopping speech: {e}")

async def speak(text):
    """Generates speech using Edge-TTS and plays it with Pygame."""
    if not text:
        return

    try:
        # Create a temporary file for the audio
        # We use a fixed name to avoid clutter, or tempfile
        # Using a fixed temp file ensures we overwrite old ones
        temp_file = os.path.join(tempfile.gettempdir(), "jarvis_speech.mp3")
        
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(temp_file)

        if pygame.mixer.get_init():
            pygame.mixer.music.load(temp_file)
            pygame.mixer.music.play()
            
            # Wait for playback to finish while keeping the loop responsive
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
                
            # Unload to release the file lock
            pygame.mixer.music.unload()
            
    except Exception as e:
        logging.error(f"Edge-TTS Error: {e}")
        # Fallback? Maybe just log for now.

def speak_sync(text):
    """Synchronous wrapper for speak (not recommended for Edge-TTS due to async nature)."""
    asyncio.run(speak(text))

def listen_sync():
    recognizer = sr.Recognizer()
    mic = sr.Microphone()
    
    try:
        with mic as source:
            # logging.info("Adjusting for ambient noise...")
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            logging.info("Listening...")
            try:
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                return None
        
        logging.info("Recognizing...")
        try:
            command = recognizer.recognize_google(audio).lower()
            logging.info(f"User said: {command}")
            return command
        except sr.UnknownValueError:
            return None
            
    except Exception as e:
        logging.error(f"Speech Error: {e}")
        return None

async def listen():
    return await asyncio.to_thread(listen_sync)
