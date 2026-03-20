import os
import re
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable


class SkillManager:
    def __init__(self, skill_dir="skill"):
        self.skill_dir = Path(skill_dir)
        self.skills = {}
        self._tool_registry: Dict[str, Callable] = {}
        self._action_handlers: Dict[str, Callable] = {}
        self.load_skills()
        self._register_default_actions()

    def _register_default_actions(self):
        from cv_ui_integration import get_desktop_actions

        async def click_action(description: str, **kwargs):
            da = get_desktop_actions()
            return await da.execute_verified("click", description)

        async def double_click_action(description: str, **kwargs):
            da = get_desktop_actions()
            return await da.double_click(description)

        async def right_click_action(description: str, **kwargs):
            da = get_desktop_actions()
            return await da.right_click(description)

        async def hover_action(description: str, **kwargs):
            da = get_desktop_actions()
            return await da.hover(description)

        async def drag_action(source: str, target: str, **kwargs):
            da = get_desktop_actions()
            return await da.drag(source, target)

        async def scroll_action(direction: str = "down", amount: int = 3, **kwargs):
            da = get_desktop_actions()
            return await da.scroll(direction, amount)

        async def type_text_action(text: str, **kwargs):
            da = get_desktop_actions()
            return await da.type_text(text)

        async def press_key_action(key: str, **kwargs):
            da = get_desktop_actions()
            return await da.press_key(key)

        self._action_handlers = {
            "click": click_action,
            "double_click": double_click_action,
            "right_click": right_click_action,
            "hover": hover_action,
            "drag": drag_action,
            "scroll": scroll_action,
            "type_text": type_text_action,
            "press_key": press_key_action,
        }

    def load_skills(self):
        """Scans the skill directory and loads metadata from SKILL.md files."""
        if not self.skill_dir.exists():
            logging.warning(f"Skill directory not found: {self.skill_dir}")
            return

        for skill_path in self.skill_dir.iterdir():
            if skill_path.is_dir():
                skill_md = skill_path / "SKILL.md"
                if skill_md.exists():
                    try:
                        content = skill_md.read_text(encoding='utf-8')
                        sanitized_content = self._sanitize_skill_content(content)
                        metadata = self._parse_frontmatter(sanitized_content)
                        if metadata and "name" in metadata:
                            name = self._sanitize_skill_name(metadata["name"])
                            self.skills[name] = {
                                "description": metadata.get("description", ""),
                                "content": sanitized_content,
                                "path": skill_path,
                                "metadata": metadata
                            }
                    except Exception as e:
                        logging.error(f"Error loading skill {skill_path}: {e}")

    def _sanitize_skill_content(self, content: str) -> str:
        if not content:
            return ""
        sanitized = content
        import re as _re
        template_patterns = [
            r'\{\{[^}]+\}\}',
            r'\{%[^%]+%\}',
            r'<\?php[^?]+\?>',
            r'<\%=[^%]+%\>',
        ]
        for pattern in template_patterns:
            sanitized = _re.sub(pattern, '[TEMPLATE_REMOVED]', sanitized)
        dangerous_patterns = [
            r'(?i)<script[^>]*>.*?</script>',
            r'(?i)javascript:',
            r'(?i)on\w+\s*=',
        ]
        for pattern in dangerous_patterns:
            sanitized = _re.sub(pattern, '[SCRIPT_REMOVED]', sanitized)
        if sanitized != content:
            logging.warning("Skill content contained template/script tags - sanitized")
        return sanitized

    def _sanitize_skill_name(self, name: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_-]', '', name)

    def _parse_frontmatter(self, content):
        """Simple regex-based frontmatter parser (avoids PyYAML dependency)."""
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        frontmatter_text = match.group(1)
        metadata = {}
        for line in frontmatter_text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip().strip('"').strip("'")
        return metadata

    def find_relevant_skills(self, user_input):
        """Finds skills relevant to the user input based on name and description."""
        relevant = []
        user_input_lower = user_input.lower()
        
        for name, data in self.skills.items():
            # Check if name or description contains any words from user input
            # Or if user input contains the skill name
            if name.lower() in user_input_lower:
                relevant.append(data)
                continue
                
            description = data["description"].lower()
            # Simple keyword matching
            keywords = name.lower().split('-')
            if any(kw in user_input_lower for kw in keywords if len(kw) > 2):
                relevant.append(data)
                
        return relevant

    def get_skill_context(self, relevant_skills):
        """Formats relevant skills into a context string for the AI."""
        if not relevant_skills:
            return ""

        context = "\n### RELEVANT SKILLS FOUND:\n"
        for skill in relevant_skills:
            context += f"\n--- SKILL: {skill['metadata'].get('name', 'Unknown')} ---\n"
            context += skill['content']
            context += "\n---------------------------\n"

        return context

    def register_tool(self, name: str, func: Callable):
        self._tool_registry[name] = func

    def register_action(self, name: str, handler: Callable):
        self._action_handlers[name] = handler

    async def execute_action(self, action_name: str, **kwargs) -> Dict[str, Any]:
        if action_name in self._action_handlers:
            try:
                handler = self._action_handlers[action_name]
                result = await handler(**kwargs)
                return {"success": True, "action": action_name, "result": result}
            except Exception as e:
                logging.error(f"Action '{action_name}' failed: {e}")
                return {"success": False, "action": action_name, "error": str(e)}
        return {"success": False, "action": action_name, "error": f"Unknown action: {action_name}"}

    def get_available_actions(self) -> List[str]:
        return list(self._action_handlers.keys())

    def get_skill_schema(self, skill_name: str) -> Optional[Dict[str, Any]]:
        if skill_name not in self.skills:
            return None

        skill = self.skills[skill_name]
        metadata = skill.get("metadata", {})

        return {
            "name": skill_name,
            "description": skill.get("description", ""),
            "content": skill.get("content", ""),
            "metadata": metadata
        }

    def execute_skill_action(self, skill_name: str, action: str, **kwargs) -> str:
        return f"Use action '{action}' from skill '{skill_name}' with params: {kwargs}"

# Singleton instance
skill_manager = SkillManager()
