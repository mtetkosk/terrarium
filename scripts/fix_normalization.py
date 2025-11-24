#!/usr/bin/env python3
"""Helper script to replace the normalization function"""

import re

NEW_FUNCTION_BODY = '''        import re
        normalized = name.lower().strip()
        
        # Remove parenthetical content like "(PA)", "(TN)", etc.
        normalized = re.sub(r'\\s*\\([^)]*\\)', '', normalized)
        
        # Remove periods from abbreviations
        normalized = normalized.replace('.', ' ')
        normalized = ' '.join(normalized.split())  # Normalize whitespace
        
        # Handle "St." vs "Saint" variations (do this before State/St normalization)
        normalized = re.sub(r'\\bst\\.\\s*', 'saint ', normalized, flags=re.IGNORECASE)
        normalized = re.sub(r'\\bst\\s+', 'saint ', normalized)  # "St Francis" -> "Saint Francis"
        normalized = normalized.replace("saint ", "st ")  # Normalize back to "st"
        
        # Handle State/St variations
        normalized = normalized.replace(" state ", " st ")
        normalized = normalized.replace("state ", "st ")
        if normalized.endswith(" state"):
            normalized = normalized[:-6] + " st"
        
        # Handle University/Univ variations - normalize to consistent form
        normalized = normalized.replace(" university ", " univ ")
        normalized = normalized.replace("univ. ", "univ ")
        normalized = normalized.replace("univ.", "univ")
        normalized = normalized.replace("university ", "univ ")
        normalized = normalized.replace(" university", " univ")
        
        # Handle hyphenated names - normalize hyphens to spaces
        normalized = normalized.replace("-", " ")
        normalized = ' '.join(normalized.split())  # Normalize whitespace again
        
        # Handle specific team name abbreviations
        normalized = re.sub(r'\\biupui\\b', 'iu indianapolis', normalized)
        normalized = re.sub(r'\\btenn-martin\\b', 'ut martin', normalized)
        normalized = re.sub(r'\\btennessee-martin\\b', 'ut martin', normalized)
        normalized = re.sub(r'\\bse\\s+', 'southeast ', normalized)
        normalized = re.sub(r'\\bsw\\s+', 'southwest ', normalized)
        normalized = re.sub(r'\\bne\\s+', 'northeast ', normalized)
        normalized = re.sub(r'\\bnw\\s+', 'northwest ', normalized)
        
        # Remove common prefixes that might differ between sources
        prefixes_to_remove = ["univ of ", "university of ", "college of "]
        for prefix in prefixes_to_remove:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
        
        # Remove common suffixes that might differ between sources
        suffixes_to_remove = [" univ", " university", " college", " state"]
        for suffix in suffixes_to_remove:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        
        # Final whitespace normalization
        normalized = ' '.join(normalized.split())
        
        return normalized'''

# Read the file
with open('src/data/scrapers/lines_scraper.py', 'r') as f:
    content = f.read()

# Find and replace the function body
old_start = "        if not name:\n            return \"\"\n        \n        normalized = name.lower().strip()"
old_end = "        return normalized"

# Use regex to find the entire function body and replace it
pattern = r'(        if not name:\s+return ""\s+normalized = name\.lower\(\)\.strip\(\)\s+).*?(        return normalized)'
replacement = r'\1' + NEW_FUNCTION_BODY.replace('\n', '\n')

content = re.sub(pattern, replacement, content, flags=re.DOTALL)

# Write back
with open('src/data/scrapers/lines_scraper.py', 'w') as f:
    f.write(content)

print("Done!")

