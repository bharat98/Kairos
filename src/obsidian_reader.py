import os
import glob
from pathlib import Path

class ObsidianReader:
    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        if not self.vault_path.exists():
            raise ValueError(f"Vault path does not exist: {self.vault_path}")

    def get_priority_files(self):
        """
        Finds markdown files in priority folders: Job, Projects, Identity.
        Also looks for fitness-related keywords.
        """
        # Define priority subfolders (case-insensitive globbing)
        priority_patterns = [
            "Job/**/*.md",
            "Projects/**/*.md",
        ]
        
        # Files that may be anywhere but have high signal
        key_filenames = [
            "README.md",
            "Identity.md",
            "*Fitness*.md",
            "*Health*.md",
        ]

        files = set()
        
        # 1. Search priority folders
        exclude_dirs = ["venv", ".venv", "node_modules", ".git", ".gemini", "data"]
        
        for pattern in priority_patterns:
            for p in self.vault_path.glob(pattern):
                if p.is_file():
                    # Check if any part of the path is in exclude_dirs
                    if not any(part in p.parts for part in exclude_dirs):
                        files.add(p)
        
        # 2. Search for key filenames anywhere in the vault
        for pattern in key_filenames:
            for p in self.vault_path.rglob(pattern):
                if p.is_file():
                    files.add(p)

        return list(files)

    def read_file_content(self, file_path: Path):
        """Reads file content with basic error handling."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading {file_path}: {e}"

    def get_all_context_text(self):
        """
        Reads all priority files and aggregates them into a structured string.
        """
        priority_files = self.get_priority_files()
        aggregated_text = []
        
        for file in priority_files:
            rel_path = file.relative_to(self.vault_path)
            content = self.read_file_content(file)
            aggregated_text.append(f"--- FILE: {rel_path} ---\n{content}\n")
            
        return "\n".join(aggregated_text)

if __name__ == "__main__":
    # Test script
    from dotenv import load_dotenv
    load_dotenv()
    
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
    if vault_path:
        reader = ObsidianReader(vault_path)
        files = reader.get_priority_files()
        print(f"Found {len(files)} priority files.")
        for f in files[:5]:
            print(f" - {f.relative_to(vault_path)}")
    else:
        print("OBSIDIAN_VAULT_PATH not set in .env")
