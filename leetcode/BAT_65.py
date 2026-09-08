def find_largest(numbers):
    # Write your code here
    innishial_value=numbers[0]
    for val in numbers:
        if val>innishial_value:
            innishial_value=val
    return innishial_value
print(find_largest([45, 12, 98, 23, 67, 34, 89, 10, 55, 76]))

# Test your code with the following test cases:
# find_largest([45, 12, 98, 23, 67, 34, 89, 10, 55, 76])
