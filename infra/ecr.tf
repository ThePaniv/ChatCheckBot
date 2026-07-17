# Chicken-and-egg: the Lambda functions need an image in this repo to be
# created. First run: terraform apply -target=aws_ecr_repository.bot,
# push the image, then run a full apply.

resource "aws_ecr_repository" "bot" {
  name = var.ecr_repo_name
}
