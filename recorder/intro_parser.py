import re
import string
from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass
class IntroMatch:
    name: str
    confidence: float
    evidence: str

class IntroParser:
    def __init__(self):
        # Stop words that can't be names
        self.stop_words = {
            "не", "что", "как", "это", "по", "но", "они", "мы", "вы", "он", "она",
            "сказал", "сказала", "так", "вот", "то", "или", "если", "когда", "тут",
            "the", "a", "an", "is", "are", "was", "were", "and", "but", "or", "so",
            "he", "she", "it", "they", "we", "you", "said", "asked", "told"
        }
        
        # Patterns for positive matches (first person)
        # Groups: 1 is the prefix, 2 is the name
        self.patterns = [
            # RU
            re.compile(r'\b(меня\s+зовут)\s+([А-Яа-яЁёA-Za-z\-]{2,}(?:\s+[А-Яа-яЁёA-Za-z\-]{2,}){0,2})\b', re.IGNORECASE),
            re.compile(r'\b(мо[её]\s+имя)\s+([А-Яа-яЁёA-Za-z\-]{2,}(?:\s+[А-Яа-яЁёA-Za-z\-]{2,}){0,2})\b', re.IGNORECASE),
            re.compile(r'\b(я)\s+([А-Яа-яЁёA-Za-z\-]{2,}(?:\s+[А-Яа-яЁёA-Za-z\-]{2,}){0,2})\b', re.IGNORECASE),
            # EN
            re.compile(r'\b(my\s+name\s+is)\s+([А-Яа-яЁёA-Za-z\-]{2,}(?:\s+[А-Яа-яЁёA-Za-z\-]{2,}){0,2})\b', re.IGNORECASE),
            re.compile(r'\b(i[\'’]m)\s+([А-Яа-яЁёA-Za-z\-]{2,}(?:\s+[А-Яа-яЁёA-Za-z\-]{2,}){0,2})\b', re.IGNORECASE),
            re.compile(r'\b(i\s+am)\s+([А-Яа-яЁёA-Za-z\-]{2,}(?:\s+[А-Яа-яЁёA-Za-z\-]{2,}){0,2})\b', re.IGNORECASE),
        ]
        
        # Negative patterns indicating quotes, questions, or third person
        self.negative_patterns = [
            re.compile(r'сказал[аои]?\b', re.IGNORECASE),
            re.compile(r'спросил[аои]?\b', re.IGNORECASE),
            re.compile(r'said\b', re.IGNORECASE),
            re.compile(r'asked\b', re.IGNORECASE),
            re.compile(r'\?', re.IGNORECASE),
            re.compile(r'тебя\s+зовут\b', re.IGNORECASE),
            re.compile(r'тво[её]\s+имя\b', re.IGNORECASE),
            re.compile(r'your\s+name\b', re.IGNORECASE),
            re.compile(r'е[гё]\s+зовут\b', re.IGNORECASE),
            re.compile(r'их\s+зовут\b', re.IGNORECASE),
            re.compile(r'his\s+name\b', re.IGNORECASE),
            re.compile(r'her\s+name\b', re.IGNORECASE),
        ]

    def _is_valid_name(self, text: str) -> bool:
        # Check token length (1-3)
        tokens = text.split()
        if not (1 <= len(tokens) <= 3):
            return False
        
        # Ensure it doesn't contain entirely stopwords
        if all(t.lower() in self.stop_words for t in tokens):
            return False
            
        # Ensure not too long total length
        if len(text) > 40:
            return False
            
        return True

    def parse_intro(self, text: str) -> Optional[IntroMatch]:
        """Parse text to find a self-introduction."""
        # 1. Check for negative markers
        for neg_p in self.negative_patterns:
            if neg_p.search(text):
                return None
                
        # 2. Check for positive matches
        best_match = None
        
        for p in self.patterns:
            for match in p.finditer(text):
                prefix = match.group(1)
                name_candidate = match.group(2).strip()
                
                # Check for bad punctuation inside the candidate or right after
                # Strip trailing punctuation from name candidate
                name_clean = name_candidate.rstrip(string.punctuation).strip()
                
                if self._is_valid_name(name_clean):
                    # Title case the extracted name
                    name_clean = name_clean.title()
                    
                    # Compute confidence based on pattern explicitly
                    confidence = 0.9 if "зовут" in prefix.lower() or "name" in prefix.lower() else 0.7
                    
                    evidence = match.group(0).strip()
                    
                    if not best_match or confidence > best_match.confidence:
                        best_match = IntroMatch(
                            name=name_clean,
                            confidence=confidence,
                            evidence=evidence
                        )
                        
        return best_match
