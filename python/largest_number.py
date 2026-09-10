def largest_number(n):  
    in_value=0
    for val in n:
        if val>in_value:
            in_value=val
    return in_value
print(largest_number([43,56,48,55]))