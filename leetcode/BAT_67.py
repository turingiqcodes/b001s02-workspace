def performance_summary(students):
    # Write your code here
    result=[]
    for key,val in students.items():
        if val>=50:
            result.append(key)
    return result

# Test your code with the following test cases:
print(performance_summary({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40, 'John': 70, 'Asha': 88, 'Vijay': 35, 'Arun': 61, 'Neha': 95}))
