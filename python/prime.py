from math import sqrt

def prime(n):
    end=int(sqrt(n))+1
    print(end)
    if n==0 or n==1:
        return False
    for i in range(2,end):
        if n%i==0:
            return False
    return True
n=4
print(prime(n))

