
import random

def generate_addition_problem():
    num1 = random.randint(10, 50)
    num2 = random.randint(5, 30)
    problem = f"{num1} + {num2}"
    answer = num1 + num2
    return problem, answer

def generate_subtraction_problem():
    num1 = random.randint(20, 70)
    num2 = random.randint(5, 40)
    # Ensure the result is not negative for simpler problems
    if num1 < num2:
        num1, num2 = num2, num1
    problem = f"{num1} - {num2}"
    answer = num1 - num2
    return problem, answer

def generate_multiplication_problem():
    num1 = random.randint(2, 12)
    num2 = random.randint(2, 10)
    problem = f"{num1} * {num2}"
    answer = num1 * num2
    return problem, answer

def generate_division_problem():
    # Ensure division results in a whole number
    num2 = random.randint(2, 10)
    num1 = num2 * random.randint(2, 12)
    problem = f"{num1} / {num2}"
    answer = num1 // num2
    return problem, answer

def generate_quiz_question(difficulty="easy"):
    problem_types = [
        generate_addition_problem,
        generate_subtraction_problem,
        generate_multiplication_problem,
        generate_division_problem
    ]
    
    # For now, difficulty can be a placeholder. We can expand this later.
    # For 'easy', we might stick to addition/subtraction.
    # For 'medium', include multiplication.
    # For 'hard', include division and more complex numbers.
    
    selected_problem_generator = random.choice(problem_types)
    return selected_problem_generator()

if __name__ == "__main__":
    problem, answer = generate_quiz_question()
    print(f"What is {problem}?")
    print(f"The answer is: {answer}")
