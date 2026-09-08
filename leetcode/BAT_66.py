def passed_students(students):
    result={}
    passed_count=0
    failed_count=0
    max_score=0
    min_score=(float("inf"))
    for val in students.values():
        if val>=50:
            passed_count+=1
        else:
            failed_count+=1
        max_score=max(max_score,val)
        min_score=min(min_score,val)


    result['passed']=passed_count
    result['failed']=failed_count
    result['highest']=max_score
    result['lowest']=min_score
    return result

    # Write your code here

# Test your code with the following test cases:
print(passed_students({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40}))
