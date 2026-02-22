import asyncio
from playwright.async_api import async_playwright
import logging

# Global WebAutomator instance
_automator = None

class WebAutomator:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._is_initialized = False

    async def ensure_initialized(self):
        if not self._is_initialized:
            try:
                self.playwright = await async_playwright().start()
                # Launch headed so the user can see it (Visualized)
                # enable_downloads=True is default, but we can configure it if needed
                self.browser = await self.playwright.chromium.launch(headless=False, slow_mo=1000, args=["--start-maximized"]) 
                self.context = await self.browser.new_context(viewport={"width": 1920, "height": 1080})
                self.page = await self.context.new_page()
                self._is_initialized = True
                logging.info("WebAutomator initialized (Headless: False).")
            except Exception as e:
                logging.error(f"Failed to initialize WebAutomator: {e}")
                raise e

    async def stop(self):
        if self._is_initialized:
            await self.context.close()
            await self.browser.close()
            await self.playwright.stop()
            self._is_initialized = False
            logging.info("WebAutomator stopped.")

    async def browse(self, url: str):
        await self.ensure_initialized()
        try:
            if not url.startswith('http'):
                url = 'https://' + url
            logging.info(f"Navigating to: {url}")
            await self.page.goto(url)
            title = await self.page.title()
            return f"Navigated to '{title}'. NOW CALL 'get_web_elements' to see what to click."
        except Exception as e:
            return f"Error navigating to {url}: {e}"

    async def get_interactive_elements(self):
        """Scrapes the page for interactive elements and assigns them numeric tags."""
        if not self._is_initialized:
             return "Error: Browser not active."
        try:
            # Inject script to find buttons, links, inputs and label them visually
            # We use a pure JS function to avoid serialization issues
            elements_list = await self.page.evaluate("""() => {
                // Helper to check visibility
                function isVisible(elem) {
                    if (!elem) return false;
                    const style = window.getComputedStyle(elem);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = elem.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }

                // Remove old attributes
                document.querySelectorAll('[data-jarvis-id]').forEach(el => el.removeAttribute('data-jarvis-id'));

                // Query potential interactive elements
                const selectors = [
                    'a[href]', 
                    'button', 
                    'input:not([type="hidden"])', 
                    'textarea', 
                    'select', 
                    '[role="button"]', 
                    '[onclick]',
                    'div[class*="button"]' // Heuristic
                ];
                
                const allElements = Array.from(document.querySelectorAll(selectors.join(',')));
                
                // Filter and map
                const interactive = [];
                let idCounter = 1;

                allElements.forEach(el => {
                    if (isVisible(el)) {
                        el.setAttribute('data-jarvis-id', idCounter);
                        
                        let text = el.innerText || el.placeholder || el.value || el.getAttribute('aria-label') || "";
                        text = text.replace(/\\s+/g, ' ').trim().substring(0, 50);
                        
                        // Heuristic for icon-only buttons
                        if (!text && (el.tagName === 'BUTTON' || el.getAttribute('role') === 'button')) {
                            const icon = el.querySelector('svg, i, img');
                            if (icon) text = "[Icon]";
                        }
                        
                        // Add basic info
                        interactive.push(`[${idCounter}] <${el.tagName.toLowerCase()}> "${text}"`);
                        idCounter++;
                    }
                });

                return interactive.join('\\n');
            }""")
            
            if not elements_list:
                return "No interactive elements found on this page."
                
            return f"Interactive Elements (ID - Tag - Content):\n{elements_list}\n\nINSTRUCTION: To interact, use 'web_click_id(id)' or 'web_type_id(id, text)'."
        except Exception as e:
            return f"Error analyzing page: {e}"

    async def click_by_id(self, ai_id: int):
        """Clicks an element by the temporary ID assigned in get_interactive_elements."""
        if not self._is_initialized:
            return "Error: Browser not active."
        try:
            selector = f'[data-jarvis-id="{ai_id}"]'
            # Check if exists first
            count = await self.page.locator(selector).count()
            if count == 0:
                 return f"Error: Element with ID [{ai_id}] not found. Did you navigate away? Call 'get_web_elements' again."
            
            # Scroll into view if needed
            element = self.page.locator(selector).first
            await element.scroll_into_view_if_needed()
            
            # Attempt click
            # Force click if needed, but try normal first
            try:
                await element.click(timeout=3000)
            except:
                await element.dispatch_event("click") # Fallback for stubborn elements
                
            return f"Clicked element [{ai_id}]."
        except Exception as e:
            return f"Error clicking ID {ai_id}: {e}"

    async def type_by_id(self, ai_id: int, text: str):
        """Types text into an element by ID."""
        if not self._is_initialized:
            return "Error: Browser not active."
        try:
            selector = f'[data-jarvis-id="{ai_id}"]'
            count = await self.page.locator(selector).count()
            if count == 0:
                 return f"Error: Element [{ai_id}] not found."
            
            element = self.page.locator(selector).first
            await element.scroll_into_view_if_needed()
            await element.fill(text)
            return f"Typed '{text}' into element [{ai_id}]."
        except Exception as e:
            return f"Error typing into ID {ai_id}: {e}"
            
    async def scroll(self, direction: str):
        """Scrolls the page."""
        if not self._is_initialized:
            return "Error: Browser not active."
        try:
            if direction.lower() == "down":
                await self.page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            elif direction.lower() == "up":
                await self.page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8)")
            elif direction.lower() == "bottom":
                await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif direction.lower() == "top":
                await self.page.evaluate("window.scrollTo(0, 0)")
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Error scrolling: {e}"

    async def press_key(self, key: str):
        if not self._is_initialized:
            return "Error: Browser not active."
        try:
            await self.page.keyboard.press(key)
            return f"Pressed key '{key}' in browser."
        except Exception as e:
            return f"Error pressing key: {e}"

    async def get_content(self):
        if not self._is_initialized:
             return "Error: Browser not active."
        try:
            content = await self.page.inner_text("body")
            return content[:4000] # Return reasonable amount of text
        except Exception as e:
            return f"Error getting content: {e}"

# Singleton management
def get_automator():
    global _automator
    if _automator is None:
        _automator = WebAutomator()
    return _automator

# --- Exported Functions for AI Tools ---

async def browse_web(url: str):
    """Opens a website using a real browser (Playwright). reliable for dynamic sites.
    Args:
        url: The URL to visit (e.g., 'https://www.google.com').
    """
    automator = get_automator()
    return await automator.browse(url)

async def get_web_elements():
    """Analyzes the current page and returns a list of interactive elements with numeric IDs (e.g., [1] Button: Login).
    ALWAYS call this before trying to click or type.
    """
    automator = get_automator()
    return await automator.get_interactive_elements()

async def web_click_id(element_id: int):
    """Clicks a web element by its assigned ID from get_web_elements.
    Args:
        element_id: The numeric ID of the element to click (e.g., 5).
    """
    automator = get_automator()
    return await automator.click_by_id(element_id)

async def web_type_id(element_id: int, text: str):
    """Types text into a web element by its assigned ID.
    Args:
        element_id: The numeric ID of the input field.
        text: The text to type.
    """
    automator = get_automator()
    return await automator.type_by_id(element_id, text)

async def web_scroll(direction: str):
    """Scrolls the web page.
    Args:
        direction: 'up', 'down', 'top', or 'bottom'.
    """
    automator = get_automator()
    return await automator.scroll(direction)

async def web_press_key(key: str):
    """Presses a keyboard key in the browser (e.g., 'Enter', 'Escape').
    Args:
        key: The key name.
    """
    automator = get_automator()
    return await automator.press_key(key)

async def web_read():
    """Reads the text content of the current web page."""
    automator = get_automator()
    return await automator.get_content()

async def close_browser():
    """Closes the web browser."""
    automator = get_automator()
    await automator.stop()
    return "Browser closed."
