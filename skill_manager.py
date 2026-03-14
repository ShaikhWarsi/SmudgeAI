import os
import re
import logging
from pathlib import Path

class SkillManager:
    def __init__(self, skill_dir="skill"):
        self.skill_dir = Path(skill_dir)
        self.skills = {} # name -> {description, content, path}
        self.load_skills()

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
                        metadata = self._parse_frontmatter(content)
                        if metadata and "name" in metadata:
                            name = metadata["name"]
                            self.skills[name] = {
                                "description": metadata.get("description", ""),
                                "content": content,
                                "path": skill_path,
                                "metadata": metadata
                            }
                    except Exception as e:
                        logging.error(f"Error loading skill {skill_path}: {e}")

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

# Singleton instance
skill_manager = SkillManager()
