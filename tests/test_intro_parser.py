import unittest
from recorder.intro_parser import IntroParser, IntroMatch

class TestIntroParser(unittest.TestCase):
    def setUp(self):
        self.parser = IntroParser()

    def test_ru_positives(self):
        tests = [
            ("Меня зовут Анна.", "Анна"),
            ("Всем привет, меня зовут Иван Иванов.", "Иван Иванов"),
            ("моё имя Борис", "Борис"),
            ("я Пётр!", "Пётр"),
            ("Я Жан-Клод.", "Жан-Клод"),
        ]
        for txt, expected in tests:
            match = self.parser.parse_intro(txt)
            self.assertIsNotNone(match, f"Failed on: {txt}")
            self.assertEqual(match.name, expected)

    def test_en_positives(self):
        tests = [
            ("Hello, my name is John.", "John"),
            ("I'm Alice Smith!", "Alice Smith"),
            ("I am Bob", "Bob"),
        ]
        for txt, expected in tests:
            match = self.parser.parse_intro(txt)
            self.assertIsNotNone(match, f"Failed on: {txt}")
            self.assertEqual(match.name, expected)

    def test_negatives(self):
        tests = [
            "Анна сказала что её зовут Маша.",
            "My name is John, he said.",
            "А тебя зовут Борис?",
            "Её зовут Анна.",
            "His name is Peter.",
        ]
        for txt in tests:
            match = self.parser.parse_intro(txt)
            self.assertIsNone(match, f"Should be none for: {txt}")

    def test_invalid_names(self):
        tests = [
            "Меня зовут что как это",
            "Я сказал что",
            "My name is said asked",
        ]
        for txt in tests:
            match = self.parser.parse_intro(txt)
            self.assertIsNone(match, f"Should be none for invalid name: {txt}")

    def test_more_negatives(self):
        tests = [
            "Я думаю",
            "Я хочу продолжить",

            "А тебя зовут Борис?",
            "Её зовут Анна.",
            "His name is Peter.",
        ]
        for txt in tests:
            match = self.parser.parse_intro(txt)
            self.assertIsNone(match, f"Should be none for: {txt}")

if __name__ == "__main__":
    unittest.main()
