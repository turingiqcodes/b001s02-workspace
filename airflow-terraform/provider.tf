terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # Added automatically to every AWS resource that supports tags.
  default_tags {
    tags = {
      StudentID = var.student_id
      Purpose   = "terraform-exercise"
    }
  }
}
