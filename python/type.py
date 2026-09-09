#list
Types=["satya",26,"se","vij","train",123]
print(Types[5:-1:-1])

#tuples
Types=("satya",26,"se","vij","train",123)
first,*middle,lastbutone,last=Types
print(first)

# list comprasions
nums=[1,2,3,4,5]
temp=[val*val for val in nums if val%2==0]
print(temp)

#set
student=set()
student.add("ravi")
student.add("raju")
student.add("sita")
student.add("ravi")
print(student)
print(student.discard("raju"))
print(student)

#union,intersection,supracion

num1={1,2,3,4,5}
num2={2,6,7,8,9}
print(num1|num2)
print(num1&num2)


#dictionarys

Student= {
    'id_101':
        {
            "name":"ram",
            "age":18,
            "adderss":"hyb",
        },
    'id_102':
        {
            "name":"sita",
            "age":20,
            "adderss":"vij",
        },
    'id_103':{
        "name":"sri",
        "age":19,
        "address":"viz",
    }
    
}
temp1=student['id_101']
temp2=student['id_102']
temp3=student['id_103']
print(student.get('id_103',0))
print(student.get('id_10',0))
print(temp1.keys())
print(temp.values())
temp2["address"]="bez"
temp2["gender"]="male"
print(temp3.pop('gender','M'))
del temp2['gender']