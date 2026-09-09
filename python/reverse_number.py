def reverse_number(n):
    rev=0
    while (n!=0):
        rem=n%10
        rev=(rev*10)+rem
        n//=10
    return rev
n=int(input("enter a number"))
print(reverse_number(n))