
from math_quiz_generator import generate_quiz_question

class StudyBuddyChatbot:
    def __init__(self):
        self.correct_answers = 0
        self.total_questions = 0
        self.learning_style_data = [] # Placeholder for adaptive learning data

    def start_quiz(self):
        print("Hello! I'm your study buddy. Let's practice some math!")
        while True:
            problem_str, correct_answer = generate_quiz_question()
            print(f"\nWhat is {problem_str}?")
            
            try:
                user_answer = float(input("Your answer: "))
            except ValueError:
                print("Please enter a number.")
                continue

            self.total_questions += 1

            if user_answer == correct_answer:
                print("That's correct! Good job!")
                self.correct_answers += 1
                self.learning_style_data.append("correct")
            else:
                print(f"Not quite. The correct answer was {correct_answer}.")
                self.learning_style_data.append("incorrect")
            
            self.provide_feedback()
            
            another_question = input("Do you want another question? (yes/no): ").lower()
            if another_question != 'yes':
                print("Thanks for studying with me! See you next time.")
                break

    def provide_feedback(self):
        if self.total_questions > 0:
            accuracy = (self.correct_answers / self.total_questions) * 100
            print(f"Your current accuracy: {accuracy:.2f}% ({self.correct_answers}/{self.total_questions})")
            
            # Basic adaptive learning feedback (placeholder)
            if self.total_questions >= 5:
                if accuracy < 60:
                    print("It seems like you might need more practice. Let's try some easier questions next time.")
                elif accuracy > 80:
                    print("You're doing great! Let's try to challenge you a bit more.")

    def adapt_to_learning_style(self):
        # This is a placeholder for more sophisticated adaptive learning logic.
        # In a real scenario, this would involve analyzing patterns in 
        # self.learning_style_data and adjusting question types, difficulty, 
        # or even explanation styles.
        pass

if __name__ == "__main__":
    chatbot = StudyBuddyChatbot()
    chatbot.start_quiz()
