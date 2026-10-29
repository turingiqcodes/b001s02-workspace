variable "student_id" {
  description = "Lowercase student ID, supplied via the TF_VAR_student_id environment variable"
  type        = string

  validation {
    condition     = can(regex("^b001[a-z0-9]+$", var.student_id))
    error_message = "student_id must be lowercase and start with b001 (e.g. b001s02)."
  }
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "ID of the training VPC"
  type        = string
}

variable "public_subnet_id" {
  description = "Public subnet for the Airflow EC2 instance"
  type        = string
}

variable "db_subnet_ids" {
  description = "At least two subnets in different AZs for the RDS subnet group"
  type        = list(string)
}

variable "ami_id" {
  description = "Ubuntu 22.04 AMI ID"
  type        = string
}

variable "my_ip_cidr" {
  description = "Your public IP in CIDR form, allowed to reach SSH and the Airflow UI"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (t3.small or larger)"
  type        = string
  default     = "t3.medium"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key to install on the instance"
  type        = string
  default     = "~/.ssh/sandbox_key.pub"
}
