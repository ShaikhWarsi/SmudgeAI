---
name: desktop-automation
description: "Automate desktop UI interactions using UIA, CV, and Vision. Use for: clicking buttons, navigating menus, filling forms, dragging elements, scrolling, keyboard shortcuts. NOT for: web automation (use browser tool), file operations (use file tool)."
metadata:
  {
    "smudgeai": { "emoji": "🖥️", "requires": { "modules": ["cv_ui_integration", "desktop_state", "task_manager"] } },
  }
---

# Desktop Automation

Use **DesktopActions** for reliable desktop UI automation. This skill provides deterministic, verified desktop interactions.

## Quick Reference

### Available Tools

```python
from cv_ui_integration import get_desktop_actions

da = get_desktop_actions()

# Click with state verification
result = await da.execute_verified("click", "Save")
result = await da.execute_verified("click", "Submit", verify=False)

# Action primitives
await da.double_click("File")      # Double click
await da.right_click("Item")      # Context menu
await da.hover("Button")           # Mouse hover (no click)

# Drag and drop
await da.drag("File", "Folder")    # Drag source to target

# Scroll
await da.scroll("down", amount=3)  # Scroll down
await da.scroll("up", amount=2)    # Scroll up
await da.scroll("right", amount=1) # Scroll right

# Keyboard shortcuts
await da.press_key("enter")
await da.select_all()
await da.copy()
await da.paste()
await da.undo()
await da.save()
await da.close_tab()
await da.new_tab()
await da.switch_app()

# Type text
await da.type_text("Hello World")

# Composite actions with verification
result = await da.execute_verified("click", "Save")
if result["success"]:
    print(f"Clicked at {result['position']}")
    print(f"State changed: {result['state_changed']}")
```

## Click Fallback Chain

The `click()` method tries methods in order:

| Method | Speed | Use Case |
|--------|-------|----------|
| **UIA** | <10ms | Native Windows accessibility APIs |
| **CV** | <50ms | OpenCV edge detection for visual elements |
| **Vision** | 500ms+ | LLM fallback (last resort) |

## State Verification

Every action captures before/after state:

```python
result = await da.execute_verified("click", "Save")
# Returns:
# {
#   "success": True,
#   "method": "uia",           # How it was clicked
#   "position": (100, 200),    # Where it was clicked
#   "verified": True,          # State changed detected
#   "state_changed": True,     # Desktop state differs
#   "before_state": "Notepad",
#   "after_state": "Notepad - Untitled"
# }
```

## Element Finding

Elements are found using fuzzy matching:

```python
# Works with partial matches
da.find_element("Save")      # Matches "Save", "Save As...", "Save Button"
da.find_element("file")       # Matches "File", "File Menu"
da.find_element("cancel")    # Matches "Cancel", "Cancel Button"
```

### Caching

Element positions are cached (100 items max) for faster repeated access:

```python
da.clear_cache()  # Clear element cache if needed
```

## Common Patterns

### Form Filling

```python
# 1. Click input field
await da.execute_verified("click", "Username")
# 2. Type text
await da.type_text("myusername")
# 3. Tab to next field
await da.press_key("tab")
# 4. Type password
await da.type_text("mypassword")
# 5. Click submit
await da.execute_verified("click", "Login")
```

### File Save Dialog

```python
# Click Save
await da.execute_verified("click", "Save")
# Type filename
await da.type_text("document.txt")
# Click OK
await da.execute_verified("click", "OK")
```

### Menu Navigation

```python
# Click menu item
await da.execute_verified("click", "File")
# Click submenu item
await da.execute_verified("click", "Open...")
# Or hover and wait
await da.hover("File")
await asyncio.sleep(0.3)
await da.execute_verified("click", "Open...")
```

### Drag and Drop

```python
# Drag file to folder
result = await da.drag("report.pdf", "Documents")
if result["success"]:
    print("File moved successfully")
```

### Scrolling

```python
# Scroll to reveal hidden content
await da.scroll("down", amount=5)
await da.scroll("up", amount=2)

# Horizontal scroll
await da.scroll("right", amount=3)
```

## Error Handling

```python
result = await da.execute_verified("click", "NonExistent")
if not result["success"]:
    print(f"Failed: {result['error']}")
    # Element not found after all retries
```

## When UIA Fails

If UIA cannot find an element, CV template matching is attempted automatically:

```python
# For icon-only buttons without text labels
result = await da.execute_verified("click", "[Icon Button]")

# For custom drawn elements
result = await da.execute_verified("click", "Custom Button")
```

## Best Practices

1. **Be specific**: "Save" works better than "save button"
2. **Wait for state**: Use `execute_verified()` for important actions
3. **Check return values**: Always verify `result["success"]`
4. **Clear cache on window change**: `da.clear_cache()` when switching windows
